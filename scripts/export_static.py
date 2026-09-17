"""Export the read-heavy fan site to static HTML for CDN hosting.

Cloudflare Pages serves the generated HTML, while Flask/Neon remain the source of
truth. The exporter batch-loads player-page data so thousands of pages do not cause
thousands of round trips to Neon.
"""
from __future__ import annotations

import os
import shutil
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from xml.sax.saxutils import escape

from flask import render_template
from sqlalchemy import select, text
from sqlalchemy.orm import joinedload

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import app, engine, SessionLocal, Club, Player, Stadium  # noqa: E402

OUT = ROOT / "public"
STATIC_SRC = ROOT / "static"
STATIC_DST = OUT / "static"
SITE_URL = os.getenv("STATIC_SITE_URL", "https://issho-jleague.pages.dev").rstrip("/")
DYNAMIC_ORIGIN = os.getenv("DYNAMIC_ORIGIN", "https://issho-jleague.onrender.com").rstrip("/")
MAX_WORKERS = int(os.getenv("STATIC_EXPORT_WORKERS", "8"))


def out_path(route: str) -> Path:
    route = route.split("?", 1)[0].strip("/")
    return OUT / route / "index.html" if route else OUT / "index.html"


def write_route(route: str, data: bytes) -> None:
    path = out_path(route)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def render_route(route: str) -> str:
    with app.test_client() as client:
        response = client.get(route, follow_redirects=True)
    if response.status_code != 200:
        raise RuntimeError(f"static export failed: {route} -> {response.status_code}")
    data = response.data
    if route in {"/players", "/schedule", "/results"}:
        data = data.replace(b"</head>", b'<script src="/static/filters.js" defer></script></head>', 1)
    write_route(route, data)
    return route


def redirect_page(target: str, title: str) -> str:
    safe = escape(target, {'"': '&quot;'})
    return f"""<!doctype html><html lang=\"ja\"><head><meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<meta http-equiv=\"refresh\" content=\"0;url={safe}\"><title>{escape(title)}</title>
<link rel=\"canonical\" href=\"{safe}\"></head><body>
<p><a href=\"{safe}\">{escape(title)}を開く</a></p></body></html>"""


def rows(sql: str):
    with engine.begin() as conn:
        return [dict(r._mapping) for r in conn.execute(text(sql))]


def batch_player_contexts():
    with SessionLocal() as db:
        players = db.scalars(
            select(Player).options(joinedload(Player.club)).order_by(Player.id)
        ).unique().all()
        clubs = db.scalars(
            select(Club).options(joinedload(Club.stadium)).order_by(Club.id)
        ).unique().all()
        # Fully load scalar attributes before leaving the session.
        for p in players:
            _ = (p.id, p.slug, p.club_id, p.name, p.number, p.position, p.goals, p.appearances, p.club.id, p.club.slug, p.club.name, p.club.league)
        for c in clubs:
            _ = (c.id, c.slug, c.name, c.league, c.stadium_id)
            if c.stadium:
                _ = (c.stadium.id, c.stadium.slug, c.stadium.name)

    club_map = {c.id: c for c in clubs}
    by_club = defaultdict(list)
    for p in players:
        by_club[p.club_id].append(p)

    standing_map = {r["club_id"]: r for r in rows("SELECT * FROM standings")}

    results_map = defaultdict(list)
    for r in rows("""
        SELECT cr.*, c.name AS club_name, c.slug AS club_slug, c.league
        FROM club_results cr JOIN clubs c ON c.id = cr.club_id
        ORDER BY cr.club_id, cr.match_date DESC, cr.id DESC
    """):
        if len(results_map[r["club_id"]]) < 5:
            results_map[r["club_id"]].append(r)

    fixture_map = defaultdict(list)
    for f in rows("""
        SELECT f.*, hc.slug AS home_slug, ac.slug AS away_slug, s.slug AS stadium_slug
        FROM fixtures f
        LEFT JOIN clubs hc ON hc.id=f.home_club_id
        LEFT JOIN clubs ac ON ac.id=f.away_club_id
        LEFT JOIN stadiums s ON s.id=f.stadium_id
        WHERE f.match_date >= CURRENT_DATE
        ORDER BY f.match_date, f.kickoff NULLS LAST
    """):
        for cid in {f.get("home_club_id"), f.get("away_club_id")}:
            if cid and len(fixture_map[cid]) < 3:
                fixture_map[cid].append(f)

    social_map = defaultdict(list)
    try:
        social_rows = rows("""
            SELECT player_id, platform, url, handle, source_url, checked_at
            FROM player_socials
            ORDER BY player_id, CASE platform
                WHEN 'Instagram' THEN 1 WHEN 'X' THEN 2 WHEN 'TikTok' THEN 3 WHEN 'YouTube' THEN 4 ELSE 9 END
        """)
    except Exception:
        social_rows = []
    for s in social_rows:
        social_map[s["player_id"]].append(s)

    def teammate_key(p):
        return (-(p.goals if p.goals is not None else -1), p.position or "", p.number is None, p.number or 9999, p.name)

    contexts = []
    for p in players:
        teammates = [x for x in sorted(by_club[p.club_id], key=teammate_key) if x.id != p.id][:8]
        contexts.append((
            p.slug,
            {
                "player": p,
                "club": club_map[p.club_id],
                "standing": standing_map.get(p.club_id),
                "recent_results": results_map.get(p.club_id, []),
                "teammates": teammates,
                "socials": social_map.get(p.id, []),
                "next_fixtures": fixture_map.get(p.club_id, []),
            },
        ))
    return contexts, clubs


def render_player(item):
    slug, context = item
    with app.test_request_context(f"/player/{slug}"):
        html = render_template("player.html", **context).encode("utf-8")
    route = f"/player/{slug}"
    write_route(route, html)
    return route


def write_sitemap(urls: list[str]) -> None:
    body = "".join(f"<url><loc>{escape(SITE_URL + u)}</loc></url>" for u in sorted(set(urls)))
    (OUT / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>',
        encoding="utf-8",
    )
    (OUT / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n", encoding="utf-8")


def main() -> None:
    app.config["STATIC_EXPORT"] = True
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    shutil.copytree(STATIC_SRC, STATIC_DST)

    player_contexts, clubs = batch_player_contexts()
    with SessionLocal() as db:
        stadiums = [s.slug for s in db.scalars(select(Stadium).order_by(Stadium.id)).all()]

    fixed_routes = ["/", "/schedule", "/standings", "/results", "/clubs", "/players", "/stadiums", "/leaderboard"]
    light_routes = list(fixed_routes)
    light_routes.extend(f"/club/{c.slug}" for c in clubs)
    light_routes.extend(f"/stadium/{slug}" for slug in stadiums)

    urls = list(light_routes)
    urls.extend(f"/player/{slug}" for slug, _ in player_contexts)

    # The small set of non-player routes can still use the Flask route functions.
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(render_route, route): route for route in light_routes}
        for future in as_completed(futures):
            future.result()

    # Player pages are rendered from one batch-loaded snapshot: no per-page Neon calls.
    completed = 0
    total = len(player_contexts)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(render_player, item): item[0] for item in player_contexts}
        for future in as_completed(futures):
            slug = futures[future]
            try:
                future.result()
            except Exception as exc:
                raise RuntimeError(f"player export failed at {slug}: {exc}") from exc
            completed += 1
            if completed % 500 == 0 or completed == total:
                print(f"player export progress: {completed}/{total}", flush=True)

    quiz = out_path("/quiz")
    quiz.parent.mkdir(parents=True, exist_ok=True)
    quiz.write_text(redirect_page(f"{DYNAMIC_ORIGIN}/quiz", "Jリーグ10問クイズ"), encoding="utf-8")
    urls.append("/quiz")

    (OUT / "_headers").write_text(
        "/static/*\n  Cache-Control: public, max-age=86400, immutable\n/*\n  Cache-Control: public, max-age=300\n",
        encoding="utf-8",
    )
    (OUT / "404.html").write_text(
        "<!doctype html><meta charset=utf-8><title>404 | 一生Jリーグ</title><h1>ページが見つかりません</h1><p><a href='/'>トップへ戻る</a></p>",
        encoding="utf-8",
    )
    write_sitemap(urls)

    page_count = len(list(OUT.rglob("index.html")))
    print(f"static export complete: pages={page_count} clubs={len(clubs)} players={len(player_contexts)} stadiums={len(stadiums)}")
    if page_count < 2200:
        raise SystemExit(f"static export coverage too small: {page_count}")


if __name__ == "__main__":
    main()
