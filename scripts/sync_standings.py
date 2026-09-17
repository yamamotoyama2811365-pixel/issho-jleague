"""J1/J2/J3の順位表をクラブURLのslugでも照合して60クラブ確実に同期する。"""
import re
import sys
import unicodedata
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from sqlalchemy import select, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import engine, SessionLocal, Club, init_db  # noqa: E402

BASE = "https://www.jleague.jp"
URLS = {k: f"{BASE}/{k.lower()}/standings/" for k in ("J1", "J2", "J3")}
HEADERS = {"User-Agent": "IsshoJLeague/1.3 (+factual standings sync)", "Accept-Language": "ja,en;q=0.5"}


def clean(v):
    return re.sub(r"\s+", " ", v or "").strip()


def norm(v):
    return re.sub(r"[\s・･]", "", unicodedata.normalize("NFKC", v or "")).lower()


def intval(v):
    m = re.search(r"-?\d+", clean(v).replace(",", ""))
    return int(m.group(0)) if m else None


def http():
    s = requests.Session()
    s.headers.update(HEADERS)
    s.mount("https://", HTTPAdapter(max_retries=Retry(total=2, connect=2, read=2, backoff_factor=0.7, status_forcelist=(429,500,502,503,504), allowed_methods=("GET",))))
    return s


def ensure_table():
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS standings (
                league VARCHAR(8) NOT NULL,
                club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
                rank INTEGER, points INTEGER, played INTEGER, wins INTEGER, draws INTEGER, losses INTEGER,
                goals_for INTEGER, goals_against INTEGER, goal_diff INTEGER, recent_form VARCHAR(32),
                source_url VARCHAR(255), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (league, club_id)
            )
        """))


def clubs_by_league():
    with SessionLocal() as db:
        rows = db.scalars(select(Club)).all()
        return {
            league: [
                {"id": c.id, "name": c.name, "slug": c.slug, "norm": norm(c.name)}
                for c in rows if c.league == league
            ]
            for league in ("J1", "J2", "J3")
        }


def identify_club(tr, clubs):
    # 1) 公式クラブURLのslugを優先。表示名の揺れ・画像アイコンに影響されない。
    for a in tr.find_all("a", href=True):
        href = a.get("href") or ""
        for club in clubs:
            if re.search(rf"/club/{re.escape(club['slug'])}/?(?:[?#].*)?$", href):
                return club
    # 2) 表示文字列によるフォールバック。
    hay = norm(tr.get_text(" "))
    for club in sorted(clubs, key=lambda c: len(c["norm"]), reverse=True):
        if club["norm"] and club["norm"] in hay:
            return club
    return None


def parse(html, league, clubs):
    soup = BeautifulSoup(html, "html.parser")
    table = next((t for t in soup.find_all("table") if all(x in clean(t.get_text(" ")) for x in ("勝点", "試合", "得失"))), None)
    if not table:
        raise RuntimeError(f"{league}: standings table not found")
    out = []
    for tr in table.find_all("tr"):
        club = identify_club(tr, clubs)
        if not club:
            continue
        cells_nodes = tr.find_all(["th", "td"])
        cells = [clean(x.get_text(" ")) for x in cells_nodes]
        if len(cells) < 9:
            continue
        club_idx = next((i for i, node in enumerate(cells_nodes) if identify_club(node, [club])), 1)
        rank = intval(cells[0])
        nums = []
        recent = None
        for cell in cells[club_idx + 1:]:
            if re.fullmatch(r"(?:[WDL]\s*){1,5}", cell):
                recent = clean(cell)
                continue
            n = intval(cell)
            if n is not None:
                nums.append(n)
        if rank is None or len(nums) < 8:
            continue
        out.append({
            "league": league, "club_id": club["id"], "rank": rank,
            "points": nums[0], "played": nums[1], "wins": nums[2], "draws": nums[3], "losses": nums[4],
            "goals_for": nums[5], "goals_against": nums[6], "goal_diff": nums[7],
            "recent_form": recent, "source_url": URLS[league],
        })
    return out


def save(rows):
    stmt = text("""
        INSERT INTO standings (league, club_id, rank, points, played, wins, draws, losses, goals_for, goals_against, goal_diff, recent_form, source_url, updated_at)
        VALUES (:league,:club_id,:rank,:points,:played,:wins,:draws,:losses,:goals_for,:goals_against,:goal_diff,:recent_form,:source_url,NOW())
        ON CONFLICT (league, club_id) DO UPDATE SET
          rank=EXCLUDED.rank, points=EXCLUDED.points, played=EXCLUDED.played, wins=EXCLUDED.wins,
          draws=EXCLUDED.draws, losses=EXCLUDED.losses, goals_for=EXCLUDED.goals_for,
          goals_against=EXCLUDED.goals_against, goal_diff=EXCLUDED.goal_diff,
          recent_form=EXCLUDED.recent_form, source_url=EXCLUDED.source_url, updated_at=NOW()
    """)
    with engine.begin() as conn:
        for row in rows:
            conn.execute(stmt, row)


def main():
    init_db()
    ensure_table()
    all_clubs = clubs_by_league()
    s = http()
    total = 0
    for league, url in URLS.items():
        r = s.get(url, timeout=22)
        r.raise_for_status()
        rows = parse(r.text, league, all_clubs[league])
        save(rows)
        total += len(rows)
        print(f"{league} standings={len(rows)}", flush=True)
    with engine.begin() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM standings")).scalar_one()
    print(f"standings total={count}", flush=True)
    if count < 60 or total < 60:
        raise SystemExit(f"standings coverage failed: fetched={total} stored={count}")


if __name__ == "__main__":
    main()
