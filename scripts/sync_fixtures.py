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
        conn.execute(text("ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS round_label VARCHAR(64)"))
        conn.execute(text("ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS source_version INTEGER NOT NULL DEFAULT 0"))
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


def resolve_streamed_html(html):
    """Reattach server-streamed HTML fragments without executing remote JavaScript."""
    soup = BeautifulSoup(html, "html.parser")
    # React inserts S:* fragments at P:* placeholders. Without this step a
    # match's away team/ticket can sit at the end of the document, outside its card.
    for source, target in re.findall(r'\$RS\("(S:[^"\s]+)","(P:[^"\s]+)"\)', html):
        fragment = soup.find(id=source)
        placeholder = soup.find("template", id=target)
        if fragment is not None and placeholder is not None:
            for child in list(fragment.contents):
                placeholder.insert_before(child.extract())
            placeholder.decompose()
            fragment.decompose()
    return soup


def node_text(node):
    return clean(node.get_text(" ")) if node else ""


def parse_html(html, league, clubs, stadium_rows):
    soup = resolve_streamed_html(html)
    club_map = {norm(c["name"]): c for c in clubs}
    stadium_map = {norm(s["name"]): s for s in stadium_rows}
    rows, seen, seen_teams = [], set(), set()
    for card in soup.select(".m-schedule"):
        anchor = card.select_one("a.m-schedule__link[href]")
        match = MATCH_RE.search(anchor["href"]) if anchor else None
        if not match or match.group(1).upper() != league:
            continue
        year, code = int(match.group(2)), match.group(3)
        key = f"{league}-{year}-{code}"
        if key in seen:
            continue
        # Exact fields inside ONE card. Never climb to a container of several games.
        home_text = node_text(card.select_one('.m-schedule__team-home .m-schedule__team-name[data-media="pc"]'))
        away_text = node_text(card.select_one('.m-schedule__team-away .m-schedule__team-name[data-media="pc"]'))
        home, away = club_map.get(norm(home_text)), club_map.get(norm(away_text))
        if not home or not away or home["id"] == away["id"]:
            raise ValueError(f"{key}: incomplete/unrecognized match teams: {home_text} / {away_text}")
        group = card.find_parent(class_="p-game-schedule__group")
        header = group.select_one(".m-section-header") if group else None
        round_match = re.search(r"第\s*(\d+)\s*節", node_text(header))
        day = re.search(r"(20\d{2})/(\d{1,2})/(\d{1,2})", node_text(header))
        if not round_match or not day:
            raise ValueError(f"{key}: missing round/date heading")
        match_date = date(*map(int, day.groups())).isoformat()
        round_label = f"第{int(round_match.group(1))}節"
        for club in (home, away):
            occurrence = (match_date[:4], round_label, club["id"])
            if occurrence in seen_teams:
                raise ValueError(f"{key}: club repeated in {round_label}: {club['name']}")
            seen_teams.add(occurrence)
        venue = node_text(card.select_one('.m-schedule__info-stadium[data-media="pc"]'))
        stadium = stadium_map.get(norm(venue))
        time_text = node_text(card.select_one(".m-schedule__time-text"))
        time_match = re.search(r"\b([01]?\d|2[0-3]):[0-5]\d\b", time_text)
        ticket = card.select_one('a[href*="jleague-ticket.jp/"]')
        rows.append({
            "match_key": key, "league": league, "match_date": match_date,
            "round_label": round_label, "source_version": 2,
            "kickoff": time_match.group(0) if time_match else None,
            "home_club_id": home["id"], "away_club_id": away["id"],
            "home_name": home["name"], "away_name": away["name"],
            "venue": venue or None, "stadium_id": stadium["id"] if stadium else None,
            "competition": f"明治安田{league}リーグ",
            "match_url": urljoin(BASE, anchor["href"].split("?")[0].split("#")[0]),
            "ticket_url": ticket["href"] if ticket else None,
        })
        seen.add(key)
    # A changed source layout must fail visibly, not publish an empty schedule.
    if not rows:
        league_links = [a for a in soup.select("a[href]") if f"/match/{league.lower()}/" in a["href"]]
        if league_links:
            raise ValueError(f"{league}: match links found but no cards parsed")
    return rows


def parse_league(league, clubs, stadium_rows):
    response = http().get(f"{BASE}/{league.lower()}/match/", timeout=30)
    response.raise_for_status()
    return parse_html(response.text, league, clubs, stadium_rows)


def save(row):
    stmt = text("""
        INSERT INTO fixtures (
            match_key, league, match_date, kickoff, home_club_id, away_club_id,
            home_name, away_name, venue, stadium_id, competition, match_url, ticket_url, round_label, source_version, updated_at
        ) VALUES (
            :match_key, :league, CAST(:match_date AS DATE), :kickoff, :home_club_id, :away_club_id,
            :home_name, :away_name, :venue, :stadium_id, :competition, :match_url, :ticket_url, :round_label, :source_version, NOW()
        )
        ON CONFLICT (match_key) DO UPDATE SET
            match_date=EXCLUDED.match_date, kickoff=EXCLUDED.kickoff,
            home_club_id=EXCLUDED.home_club_id, away_club_id=EXCLUDED.away_club_id,
            home_name=EXCLUDED.home_name, away_name=EXCLUDED.away_name,
            venue=EXCLUDED.venue, stadium_id=EXCLUDED.stadium_id,
            competition=EXCLUDED.competition, match_url=EXCLUDED.match_url,
            ticket_url=EXCLUDED.ticket_url, round_label=EXCLUDED.round_label,
            source_version=EXCLUDED.source_version, updated_at=NOW()
    """)
    with engine.begin() as conn:
        conn.execute(stmt, row)


def main():
    init_db()
    ensure_table()
    clubs, stadium_rows = db_index()
    saved = 0
    ticketed = 0

    # Parse and validate all leagues before making any data changes.
    parsed = {league: parse_league(league, clubs, stadium_rows) for league in LEAGUES}
    for league, rows in parsed.items():
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
