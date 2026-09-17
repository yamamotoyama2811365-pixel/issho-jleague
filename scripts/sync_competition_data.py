"""週末用: J1/J2/J3の順位表と各クラブの戦績をJリーグ公式から同期する。

画像・動画・記事本文は保存せず、順位・勝点・試合数・得失点、
試合日・対戦相手・スコア・会場・大会・入場者数などの事実データのみ保存する。
"""
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from sqlalchemy import select, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import engine, SessionLocal, Club, init_db  # noqa: E402

BASE = "https://www.jleague.jp"
HEADERS = {
    "User-Agent": "IsshoJLeague/1.3 (+factual weekend sync)",
    "Accept-Language": "ja,en;q=0.5",
}
STANDINGS_URLS = {
    "J1": f"{BASE}/j1/standings/",
    "J2": f"{BASE}/j2/standings/",
    "J3": f"{BASE}/j3/standings/",
}


def session():
    s = requests.Session()
    s.headers.update(HEADERS)
    s.mount("https://", HTTPAdapter(max_retries=Retry(
        total=2, connect=2, read=2, backoff_factor=0.7,
        status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",)
    )))
    return s


def clean(value):
    return re.sub(r"\s+", " ", value or "").strip()


def norm(value):
    value = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"[\s・･]", "", value).lower()


def first_int(value):
    m = re.search(r"-?\d+", clean(value).replace(",", ""))
    return int(m.group(0)) if m else None


def ensure_tables():
    statements = [
        """
        CREATE TABLE IF NOT EXISTS standings (
            league VARCHAR(8) NOT NULL,
            club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
            rank INTEGER,
            points INTEGER,
            played INTEGER,
            wins INTEGER,
            draws INTEGER,
            losses INTEGER,
            goals_for INTEGER,
            goals_against INTEGER,
            goal_diff INTEGER,
            recent_form VARCHAR(32),
            source_url VARCHAR(255),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (league, club_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS club_results (
            id BIGSERIAL PRIMARY KEY,
            club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
            match_date DATE NOT NULL,
            kickoff VARCHAR(16),
            opponent VARCHAR(120) NOT NULL,
            venue VARCHAR(160),
            result VARCHAR(8),
            club_score INTEGER,
            opponent_score INTEGER,
            competition VARCHAR(120) NOT NULL,
            attendance INTEGER,
            source_url VARCHAR(255),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (club_id, match_date, opponent, competition)
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_club_results_date ON club_results(match_date DESC)",
        "CREATE INDEX IF NOT EXISTS ix_club_results_club ON club_results(club_id, match_date DESC)",
    ]
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


def club_index():
    with SessionLocal() as db:
        clubs = db.scalars(select(Club)).all()
        return [
            {
                "id": c.id,
                "league": c.league,
                "name": c.name,
                "slug": c.slug,
                "source_url": c.source_url,
                "norm": norm(c.name),
            }
            for c in clubs
        ]


def match_club(raw, clubs, league=None):
    hay = norm(raw)
    candidates = [c for c in clubs if not league or c["league"] == league]
    candidates.sort(key=lambda c: len(c["norm"]), reverse=True)
    for c in candidates:
        if c["norm"] and c["norm"] in hay:
            return c
    return None


def parse_standings(html, league, clubs):
    soup = BeautifulSoup(html, "html.parser")
    target = None
    for table in soup.find_all("table"):
        txt = clean(table.get_text(" "))
        if "勝点" in txt and "試合" in txt and "得失" in txt:
            target = table
            break
    if not target:
        raise RuntimeError(f"{league}: standings table not found")

    rows = []
    for tr in target.find_all("tr"):
        cells = [clean(x.get_text(" ")) for x in tr.find_all(["th", "td"])]
        if len(cells) < 8 or "クラブ" in " ".join(cells[:3]):
            continue
        club = match_club(" ".join(cells[:3]), clubs, league)
        if not club:
            continue
        rank = first_int(cells[0])
        # Clubセルより後ろから数値列を順に拾う: 勝点,試合,勝,分,負,得点,失点,得失
        team_idx = next((i for i, cell in enumerate(cells) if club["norm"] in norm(cell)), 1)
        numeric = []
        recent = None
        for cell in cells[team_idx + 1:]:
            if re.fullmatch(r"(?:[WDL]\s*){1,5}", cell):
                recent = clean(cell)
                continue
            n = first_int(cell)
            if n is not None:
                numeric.append(n)
        if rank is None or len(numeric) < 8:
            continue
        rows.append({
            "league": league,
            "club_id": club["id"],
            "rank": rank,
            "points": numeric[0],
            "played": numeric[1],
            "wins": numeric[2],
            "draws": numeric[3],
            "losses": numeric[4],
            "goals_for": numeric[5],
            "goals_against": numeric[6],
            "goal_diff": numeric[7],
            "recent_form": recent,
            "source_url": STANDINGS_URLS[league],
        })
    return rows


def save_standings(rows):
    sql = text("""
        INSERT INTO standings (
            league, club_id, rank, points, played, wins, draws, losses,
            goals_for, goals_against, goal_diff, recent_form, source_url, updated_at
        ) VALUES (
            :league, :club_id, :rank, :points, :played, :wins, :draws, :losses,
            :goals_for, :goals_against, :goal_diff, :recent_form, :source_url, NOW()
        )
        ON CONFLICT (league, club_id) DO UPDATE SET
            rank=EXCLUDED.rank,
            points=EXCLUDED.points,
            played=EXCLUDED.played,
            wins=EXCLUDED.wins,
            draws=EXCLUDED.draws,
            losses=EXCLUDED.losses,
            goals_for=EXCLUDED.goals_for,
            goals_against=EXCLUDED.goals_against,
            goal_diff=EXCLUDED.goal_diff,
            recent_form=EXCLUDED.recent_form,
            source_url=EXCLUDED.source_url,
            updated_at=NOW()
    """)
    with engine.begin() as conn:
        for row in rows:
            conn.execute(sql, row)


def club_root_url(club):
    url = club.get("source_url") or f"{BASE}/club/{club['slug']}/"
    url = re.sub(r"/player/?(?:[?#].*)?$", "/", url)
    if not url.endswith("/"):
        url += "/"
    return url


def parse_club_results(html, club, source_url):
    soup = BeautifulSoup(html, "html.parser")
    target = None
    for table in soup.find_all("table"):
        txt = clean(table.get_text(" "))
        if all(k in txt for k in ("年月日", "対戦相手", "スコア", "大会")):
            target = table
            break
    if not target:
        return []

    rows = []
    for tr in target.find_all("tr"):
        cells = [clean(x.get_text(" ")) for x in tr.find_all(["th", "td"])]
        if len(cells) < 6 or "年月日" in cells[0]:
            continue
        dm = re.search(r"(\d{2})/(\d{1,2})/(\d{1,2})", cells[0])
        if not dm:
            continue
        score_idx = next((i for i, c in enumerate(cells) if re.search(r"[勝負分]?\s*\d+\s*-\s*\d+", c)), None)
        if score_idx is None or score_idx < 3:
            continue
        sm = re.search(r"([勝負分])?\s*(\d+)\s*-\s*(\d+)", cells[score_idx])
        if not sm:
            continue
        year = 2000 + int(dm.group(1))
        match_date = f"{year:04d}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}"
        kickoff = cells[1] if len(cells) > 1 else None
        opponent = cells[2] if len(cells) > 2 else ""
        venue = cells[3] if len(cells) > 3 else None
        competition = cells[score_idx + 1] if len(cells) > score_idx + 1 else ""
        attendance = first_int(cells[-1]) if len(cells) >= 10 else None
        if not opponent or not competition:
            continue
        rows.append({
            "club_id": club["id"],
            "match_date": match_date,
            "kickoff": kickoff,
            "opponent": opponent[:120],
            "venue": (venue or "")[:160] or None,
            "result": sm.group(1),
            "club_score": int(sm.group(2)),
            "opponent_score": int(sm.group(3)),
            "competition": competition[:120],
            "attendance": attendance,
            "source_url": source_url,
        })
    return rows


def save_results(rows):
    sql = text("""
        INSERT INTO club_results (
            club_id, match_date, kickoff, opponent, venue, result,
            club_score, opponent_score, competition, attendance, source_url, updated_at
        ) VALUES (
            :club_id, CAST(:match_date AS DATE), :kickoff, :opponent, :venue, :result,
            :club_score, :opponent_score, :competition, :attendance, :source_url, NOW()
        )
        ON CONFLICT (club_id, match_date, opponent, competition) DO UPDATE SET
            kickoff=EXCLUDED.kickoff,
            venue=EXCLUDED.venue,
            result=EXCLUDED.result,
            club_score=EXCLUDED.club_score,
            opponent_score=EXCLUDED.opponent_score,
            attendance=EXCLUDED.attendance,
            source_url=EXCLUDED.source_url,
            updated_at=NOW()
    """)
    with engine.begin() as conn:
        for row in rows:
            conn.execute(sql, row)


def fetch_one_club(club):
    url = club_root_url(club)
    s = session()
    r = s.get(url, timeout=22)
    r.raise_for_status()
    return club, parse_club_results(r.text, club, url)


def main():
    init_db()
    ensure_tables()
    clubs = club_index()

    standing_total = 0
    s = session()
    for league, url in STANDINGS_URLS.items():
        r = s.get(url, timeout=22)
        r.raise_for_status()
        rows = parse_standings(r.text, league, clubs)
        save_standings(rows)
        standing_total += len(rows)
        print(f"{league} standings={len(rows)}", flush=True)

    result_total = 0
    clubs_with_results = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {pool.submit(fetch_one_club, club): club for club in clubs}
        for future in as_completed(jobs):
            club = jobs[future]
            try:
                _, rows = future.result()
                if rows:
                    save_results(rows)
                    result_total += len(rows)
                    clubs_with_results += 1
                print(f"{club['league']} {club['name']}: results={len(rows)}", flush=True)
            except Exception as exc:
                print(f"ERROR {club['league']} {club['name']}: {exc}", flush=True)

    with engine.begin() as conn:
        standing_count = conn.execute(text("SELECT COUNT(*) FROM standings")).scalar_one()
        result_count = conn.execute(text("SELECT COUNT(*) FROM club_results")).scalar_one()

    print(
        f"competition sync complete: standings={standing_count} "
        f"club_results={result_count} fetched_results={result_total}",
        flush=True,
    )
    if standing_count < 60 or clubs_with_results < 50:
        raise SystemExit(
            f"competition coverage failed: standings={standing_count} clubs_with_results={clubs_with_results}"
        )


if __name__ == "__main__":
    main()
