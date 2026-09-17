"""Export the read-heavy fan site to static HTML for CDN hosting.

The Flask app remains the source of truth and Neon remains the data store. This script
renders public GET pages into ./public so Cloudflare Pages (or any static host) can
serve them without a sleeping web process. Quiz is intentionally redirected to the
API-backed Render app until its write API is centralized.
"""
from __future__ import annotations

import os
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# DATABASE_URL must be set by CI before importing the Flask app.
from app import app, SessionLocal, Club, Player, Stadium  # noqa: E402

OUT = ROOT / "public"
STATIC_SRC = ROOT / "static"
STATIC_DST = OUT / "static"
SITE_URL = os.getenv("STATIC_SITE_URL", "https://issho-jleague.pages.dev").rstrip("/")
DYNAMIC_ORIGIN = os.getenv("DYNAMIC_ORIGIN", "https://issho-jleague.onrender.com").rstrip("/")
MAX_WORKERS = int(os.getenv("STATIC_EXPORT_WORKERS", "8"))


def out_path(route: str) -> Path:
    route = route.split("?", 1)[0].strip("/")
    return OUT / route / "index.html" if route else OUT / "index.html"


def render_route(route: str) -> str:
    with app.test_client() as client:
        response = client.get(route, follow_redirects=True)
    if response.status_code != 200:
        raise RuntimeError(f"static export failed: {route} -> {response.status_code}")
    path = out_path(route)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.data)
    return route


def redirect_page(target: str, title: str) -> str:
    safe = escape(target, {'"': '&quot;'})
    return f"""<!doctype html><html lang=\"ja\"><head><meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<meta http-equiv=\"refresh\" content=\"0;url={safe}\"><title>{escape(title)}</title>
<link rel=\"canonical\" href=\"{safe}\"></head><body>
<p><a href=\"{safe}\">{escape(title)}を開く</a></p></body></html>"""


def write_sitemap(urls: list[str]) -> None:
    body = "".join(f"<url><loc>{escape(SITE_URL + u)}</loc></url>" for u in sorted(set(urls)))
    (OUT / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>',
        encoding="utf-8",
    )
    (OUT / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n", encoding="utf-8")


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    shutil.copytree(STATIC_SRC, STATIC_DST)

    fixed_routes = [
        "/", "/schedule", "/standings", "/results", "/clubs", "/players",
        "/stadiums", "/leaderboard",
    ]

    with SessionLocal() as db:
        clubs = [(c.slug, c.id) for c in db.query(Club).all()]
        players = [p.slug for p in db.query(Player).all()]
        stadiums = [s.slug for s in db.query(Stadium).all()]

    urls = list(fixed_routes)
    urls.extend(f"/club/{slug}" for slug, _ in clubs)
    urls.extend(f"/player/{slug}" for slug in players)
    urls.extend(f"/stadium/{slug}" for slug in stadiums)

    completed = 0
    total = len(urls)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(render_route, route): route for route in urls}
        for future in as_completed(futures):
            route = futures[future]
            try:
                future.result()
            except Exception as exc:
                raise RuntimeError(f"static export failed at {route}: {exc}") from exc
            completed += 1
            if completed % 250 == 0 or completed == total:
                print(f"static export progress: {completed}/{total}", flush=True)

    # Write actions remain on the backend for now, so the static shell never needs
    # CORS credentials or database secrets in the browser.
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
    print(f"static export complete: pages={page_count} clubs={len(clubs)} players={len(players)} stadiums={len(stadiums)}")
    if page_count < 2200:
        raise SystemExit(f"static export coverage too small: {page_count}")


if __name__ == "__main__":
    main()
