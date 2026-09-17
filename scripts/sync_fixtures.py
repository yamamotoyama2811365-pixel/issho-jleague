"""J1/J2/J3の直近日程・会場・試合別チケットURLを軽量同期する。

公式の日程ページ3枚だけを読み、各試合カードから事実情報を取得する。
試合詳細30ページ等を個別巡回しないため、週末同期の通信量を抑える。
"""
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from sqlalchemy import select, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import engine, SessionLocal, Club, Stadium, init_db  # noqa: E402

BASE = "https://www.jleague.jp"
LEAGUES = ("J1", "J2", "J3")
HEADERS = {"User-Agent": "IsshoJLeague/1.6 (+light fixture ticket sync)", "Accept-Language": "ja,en;q=0.5"}
MATCH_RE = re.compile(r"/match/(j1|j2|j3)/(20\d{2})/(\d{6})/?")


def http():
    s = requests.Session()
    s.headers.update(HEADERS)
    s.mount("https://", HTTPAdapter(max_retries=Retry(
        total=2, connect=2, read=2, backoff_factor=0.7,
        status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",)
    )))
    return s


def clean(v):
    return re.sub(r"\s+", " ", v or "").strip()


def norm(v):
    value = unicodedata.normalize("NFKC", v or "").casefold()
    return re.sub(r"[^\wぁ-んァ-ヶ一-龠々ー]", "", value)


def ensure_table():
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS fixtures (
                match_key VARCHAR(64) PRIMARY KEY,
                league VARCHAR(8) NOT NULL,
                match_date DATE NOT NULL,
                kickoff VARCHAR(16),
                home_club_id INTEGER REFERENCES clubs(id) ON DELETE SET NULL,
                away_club_id INTEGER REFERENCES clubs(id) ON DELETE SET NULL,
                home_name VARCHAR(120) NOT NULL,
                away_name VARCHAR(120) NOT NULL,
                venue VARCHAR(160),
                stadium_id INTEGER REFERENCES stadiums(id) ON DELETE SET NULL,
                competition VARCHAR(120),
                match_url VARCHAR(255) NOT NULL,
                ticket_url VARCHAR(255),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_fixtures_date ON fixtures(match_date, kickoff)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_fixtures_home ON fixtures(home_club_id, match_date)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_fixtures_away ON fixtures(away_club_id, match_date)"))


def db_index():
    with SessionLocal() as db:
        clubs = db.scalars(select(Club)).all()
        stadiums = db.scalars(select(Stadium)).all()
    club_rows = sorted(
        [{"id": c.id, "name": c.name, "slug": c.slug, "norm": norm(c.name)} for c in clubs],
        key=lambda x: len(x["norm"]), reverse=True,
    )
    stadium_rows = sorted(
        [{"id": s.id, "name": s.name, "slug": s.slug, "norm": norm(s.name)} for s in stadiums],
        key=lambda x: len(x["norm"]), reverse=True,
    )
    return club_rows, stadium_rows


def identify_teams(raw_text, clubs):
    hay = norm(raw_text)
    hits = []
    for club in clubs:
        pos = hay.find(club["norm"])
        if pos >= 0:
            hits.append((pos, -len(club["norm"]), club))
    hits.sort(key=lambda x: (x[0], x[1]))
    teams = []
    used = set()
    for _, _, club in hits:
        if club["id"] not in used:
            used.add(club["id"])
            teams.append(club)
        if len(teams) == 2:
            break
    return teams


def identify_stadium(raw_text, stadium_rows):
    hay = norm(raw_text)
    for stadium in stadium_rows:
        if stadium["norm"] and stadium["norm"] in hay:
            return stadium
    return None


def card_context(match_anchor, clubs):
    """対戦カードの範囲まで親要素を上がり、2クラブ＋チケットを同じ範囲で確保する。"""
    best_text = clean(match_anchor.get_text(" "))
    best_node = match_anchor
    node = match_anchor
    for _ in range(9):
        text_value = clean(node.get_text(" "))
        teams = identify_teams(text_value, clubs)
        if len(teams) >= 2:
            best_text = text_value
            best_node = node
            # 2クラブが揃い、チケットリンクも含めばこのカードで確定。
            if any("jleague-ticket.jp" in (a.get("href") or "") for a in node.find_all("a", href=True)):
                break
        node = getattr(node, "parent", None)
        if node is None or len(clean(node.get_text(" "))) > 4500:
            break
    return best_node, best_text


def ticket_in(node):
    for a in node.find_all("a", href=True):
        href = a.get("href") or ""
        if "jleague-ticket.jp" in href:
            return href
    return None


def parse_league(league, clubs, stadium_rows):
    url = f"{BASE}/{league.lower()}/match/"
    r = http().get(url, timeout=25)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    rows = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a.get("href") or ""
        m = MATCH_RE.search(href)
        if not m or m.group(1).upper() != league:
            continue
        match_url = urljoin(BASE, href.split("?")[0].split("#")[0])
        if match_url in seen:
            continue
        seen.add(match_url)

        year = int(m.group(2))
        code = m.group(3)
        match_date = date(year, int(code[:2]), int(code[2:4]))

        card, card_text = card_context(a, clubs)
        teams = identify_teams(card_text, clubs)
        if len(teams) < 2:
            print(f"WARN teams not found in schedule card: {match_url} text={card_text[:180]}", flush=True)
            continue
        home, away = teams[0], teams[1]

        tm = re.search(r"\b([01]?\d|2[0-3]):[0-5]\d\b", card_text)
        kickoff = tm.group(0) if tm else None
        stadium = identify_stadium(card_text, stadium_rows)
        ticket_url = ticket_in(card)

        rows.append({
            "match_key": f"{league}-{year}-{code}",
            "league": league,
            "match_date": match_date.isoformat(),
            "kickoff": kickoff,
            "home_club_id": home["id"],
            "away_club_id": away["id"],
            "home_name": home["name"],
            "away_name": away["name"],
            "venue": stadium["name"] if stadium else None,
            "stadium_id": stadium["id"] if stadium else None,
            "competition": f"明治安田{league}リーグ",
            "match_url": match_url,
            "ticket_url": ticket_url,
        })
    return rows


def save(row):
    stmt = text("""
        INSERT INTO fixtures (
            match_key, league, match_date, kickoff, home_club_id, away_club_id,
            home_name, away_name, venue, stadium_id, competition, match_url, ticket_url, updated_at
        ) VALUES (
            :match_key, :league, CAST(:match_date AS DATE), :kickoff, :home_club_id, :away_club_id,
            :home_name, :away_name, :venue, :stadium_id, :competition, :match_url, :ticket_url, NOW()
        )
        ON CONFLICT (match_key) DO UPDATE SET
            match_date=EXCLUDED.match_date, kickoff=EXCLUDED.kickoff,
            home_club_id=EXCLUDED.home_club_id, away_club_id=EXCLUDED.away_club_id,
            home_name=EXCLUDED.home_name, away_name=EXCLUDED.away_name,
            venue=EXCLUDED.venue, stadium_id=EXCLUDED.stadium_id,
            competition=EXCLUDED.competition, match_url=EXCLUDED.match_url,
            ticket_url=COALESCE(EXCLUDED.ticket_url, fixtures.ticket_url), updated_at=NOW()
    """)
    with engine.begin() as conn:
        conn.execute(stmt, row)


def main():
    init_db()
    ensure_table()
    clubs, stadium_rows = db_index()
    saved = 0
    ticketed = 0

    for league in LEAGUES:
        rows = parse_league(league, clubs, stadium_rows)
        for row in rows:
            save(row)
        saved += len(rows)
        ticketed += sum(bool(r["ticket_url"]) for r in rows)
        print(
            f"{league}: fixtures={len(rows)} ticket_links={sum(bool(r['ticket_url']) for r in rows)} "
            f"venues={sum(bool(r['stadium_id']) for r in rows)}",
            flush=True,
        )

    with engine.begin() as conn:
        upcoming = conn.execute(text("SELECT COUNT(*) FROM fixtures WHERE match_date >= CURRENT_DATE")).scalar_one()
        upcoming_ticketed = conn.execute(text("SELECT COUNT(*) FROM fixtures WHERE match_date >= CURRENT_DATE AND ticket_url IS NOT NULL")).scalar_one()
    print(f"fixtures saved={saved} ticketed={ticketed} upcoming={upcoming} upcoming_ticketed={upcoming_ticketed}", flush=True)
    if saved == 0:
        raise SystemExit("fixture sync failed: no fixtures parsed")


if __name__ == "__main__":
    main()
