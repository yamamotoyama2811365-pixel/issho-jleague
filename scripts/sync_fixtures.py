"""J1/J2/J3の直近日程と試合別JリーグチケットURLを同期する。

公式日程ページから試合詳細URLと同じ試合カード内のチケット購入URLを拾い、
各試合詳細ページから日時・ホーム/アウェイ・会場を照合して保存する。
画像や記事本文は保存しない。
"""
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
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
HEADERS = {"User-Agent": "IsshoJLeague/1.5 (+fixture and ticket sync)", "Accept-Language": "ja,en;q=0.5"}
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
    """全半角・空白・句読点差を吸収してクラブ/会場名を照合する。"""
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
    club_rows = [
        {"id": c.id, "name": c.name, "slug": c.slug, "norm": norm(c.name)}
        for c in clubs
    ]
    stadium_rows = sorted(
        [{"id": s.id, "name": s.name, "slug": s.slug, "norm": norm(s.name)} for s in stadiums],
        key=lambda x: len(x["norm"]), reverse=True,
    )
    return club_rows, stadium_rows


def nearest_ticket_link(match_anchor):
    """日程ページ上で試合詳細リンクと同じカードにあるチケット購入リンクを探す。"""
    node = match_anchor
    for _ in range(10):
        node = getattr(node, "parent", None)
        if node is None:
            break
        links = node.find_all("a", href=True)
        tickets = [a for a in links if "jleague-ticket.jp" in (a.get("href") or "")]
        if tickets:
            return tickets[0].get("href")
        # 親を上がり過ぎると別試合のチケットを拾うので、巨大ブロックになる前に止める。
        if len(clean(node.get_text(" "))) > 3500:
            break
    return None


def collect_matches(league):
    url = f"{BASE}/{league.lower()}/match/"
    r = http().get(url, timeout=25)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
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
        out.append({
            "match_url": match_url,
            "ticket_url": nearest_ticket_link(a),
        })
    return out


def identify_teams(page_text, clubs):
    """試合詳細本文に出る正式クラブ名の出現順からホーム→アウェイを判定する。"""
    hay = norm(page_text)
    hits = []
    for club in clubs:
        needle = club["norm"]
        if not needle:
            continue
        pos = hay.find(needle)
        if pos >= 0:
            hits.append((pos, -len(needle), club))
    hits.sort(key=lambda x: (x[0], x[1]))
    teams = []
    seen = set()
    for _, _, club in hits:
        if club["id"] in seen:
            continue
        seen.add(club["id"])
        teams.append(club)
        if len(teams) == 2:
            break
    return teams


def identify_stadium(page_text, stadium_rows):
    hay = norm(page_text)
    for stadium in stadium_rows:
        if stadium["norm"] and stadium["norm"] in hay:
            return stadium
    return None


def parse_detail(item, clubs, stadium_rows):
    match_url = item["match_url"]
    m = MATCH_RE.search(match_url)
    if not m:
        return None
    league = m.group(1).upper()
    year = int(m.group(2))
    code = m.group(3)
    month, day = int(code[:2]), int(code[2:4])
    match_date = date(year, month, day)

    r = http().get(match_url, timeout=25)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    scope = soup.find("main") or soup
    page_text = clean(scope.get_text(" "))

    teams = identify_teams(page_text, clubs)
    if len(teams) < 2:
        title = clean(soup.title.get_text(" ") if soup.title else "")
        print(f"WARN teams not identified: title={title[:140]}", flush=True)
        return None
    home, away = teams[0], teams[1]

    kickoff = None
    strings = [clean(s) for s in scope.stripped_strings if clean(s)]
    for i, value in enumerate(strings):
        if value.upper() == "KICK OFF":
            for cand in strings[i + 1:i + 6]:
                if re.fullmatch(r"\d{1,2}:\d{2}", cand):
                    kickoff = cand
                    break
            if kickoff:
                break
    if not kickoff:
        tm = re.search(r"\b([01]?\d|2[0-3]):[0-5]\d\b", page_text)
        kickoff = tm.group(0) if tm else None

    stadium = identify_stadium(page_text, stadium_rows)

    ticket_url = item.get("ticket_url")
    if not ticket_url:
        for a in scope.find_all("a", href=True):
            href = a.get("href") or ""
            if "jleague-ticket.jp" in href:
                ticket_url = href
                break

    title = clean(soup.title.get_text(" ") if soup.title else "")
    competition = f"明治安田{league}リーグ" if "明治安田" in title else league

    return {
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
        "competition": competition,
        "match_url": match_url,
        "ticket_url": ticket_url,
    }


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
    matches = []
    for league in LEAGUES:
        found = collect_matches(league)
        print(
            f"{league}: match URLs={len(found)} ticket candidates={sum(bool(x.get('ticket_url')) for x in found)}",
            flush=True,
        )
        matches.extend(found)

    saved = 0
    ticketed = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {pool.submit(parse_detail, item, clubs, stadium_rows): item for item in matches}
        for future in as_completed(jobs):
            item = jobs[future]
            try:
                row = future.result()
                if not row:
                    print(f"WARN parse failed: {item['match_url']}", flush=True)
                    continue
                save(row)
                saved += 1
                ticketed += int(bool(row["ticket_url"]))
                print(
                    f"{row['match_date']} {row['home_name']} vs {row['away_name']} "
                    f"venue={row['venue']} ticket={'yes' if row['ticket_url'] else 'no'}",
                    flush=True,
                )
            except Exception as exc:
                print(f"ERROR {item['match_url']}: {exc}", flush=True)

    with engine.begin() as conn:
        upcoming = conn.execute(text("SELECT COUNT(*) FROM fixtures WHERE match_date >= CURRENT_DATE")).scalar_one()
        upcoming_ticketed = conn.execute(text("SELECT COUNT(*) FROM fixtures WHERE match_date >= CURRENT_DATE AND ticket_url IS NOT NULL")).scalar_one()
    print(f"fixtures saved={saved} ticketed={ticketed} upcoming={upcoming} upcoming_ticketed={upcoming_ticketed}", flush=True)
    if saved == 0:
        raise SystemExit("fixture sync failed: no fixtures parsed")


if __name__ == "__main__":
    main()
