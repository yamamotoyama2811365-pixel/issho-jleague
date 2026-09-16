"""2026/27シーズンのJリーグ公式ページから事実データだけを同期する。

画像、ロゴ、エンブレム、公式紹介文、公式座席図は保存・転載しない。
保存するのは選手名・背番号・ポジション・出生地・生年月日・身長体重・
出場/得点、スタジアム名・入場可能数・住所などの事実項目と出典URLのみ。

通常のWebデプロイではこの同期を実行しない。
RUN_FULL_SYNC=1 が設定された専用更新ジョブでのみ実行する。
"""
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from sqlalchemy import select
from slugify import slugify

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import SessionLocal, Club, Player, Stadium, init_db  # noqa: E402

BASE = "https://www.jleague.jp"
UA = "IsshoJLeague/1.2 (+factual roster sync)"
HEADERS = {"User-Agent": UA, "Accept-Language": "ja,en;q=0.5"}


def new_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    s.mount("https://", HTTPAdapter(max_retries=Retry(
        total=2, connect=2, read=2, backoff_factor=0.7,
        status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",)
    )))
    return s


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def parse_int(text):
    m = re.search(r"\d[\d,]*", text or "")
    return int(m.group(0).replace(",", "")) if m else None


def absolute(href):
    return urljoin(BASE, href or "")


def unique_player_slug(club_slug, name, number):
    n = slugify(name, allow_unicode=False) or slugify(name, allow_unicode=True) or "player"
    return f"{club_slug}-{n}-{number or 'x'}"[:155]


def player_from_cells(cells, club_slug, source_url):
    if len(cells) < 6:
        return None
    first = clean(cells[0].get_text(" "))
    if not first or first == "選手" or "出生地" in first:
        return None
    pm = re.search(r"\b(GK|DF|MF|FW)\s*(\d{1,3})?", first)
    a = cells[0].find("a", href=re.compile(r"/player/\d+/?"))
    name = clean(a.get_text(" ")) if a else ""
    if not name:
        raw = first.replace("HG", " ")
        if pm:
            raw = clean(raw[:pm.start()] + " " + raw[pm.end():])
        name = clean(raw)
    if not name:
        return None
    position = pm.group(1) if pm else None
    number = int(pm.group(2)) if pm and pm.group(2) else None
    birthplace = clean(cells[1].get_text(" ")) or None
    birthdate = clean(cells[2].get_text(" ")) or None
    hw = clean(cells[3].get_text(" "))
    hm = re.search(r"(\d{3})\s*/\s*(\d{2,3})", hw)
    app_text = clean(cells[4].get_text(" "))
    goal_text = clean(cells[5].get_text(" "))
    detail_url = absolute(a.get("href")) if a else source_url
    return {
        "name": name, "position": position, "number": number, "birthplace": birthplace,
        "birthdate": birthdate, "height_cm": int(hm.group(1)) if hm else None,
        "weight_kg": int(hm.group(2)) if hm else None,
        "appearances": parse_int(app_text) if app_text != "-" else None,
        "goals": parse_int(goal_text) if goal_text != "-" else None,
        "source_url": detail_url,
        "slug": unique_player_slug(club_slug, name, number),
    }


def player_from_anchor(a, club_slug, source_url):
    """tableタグが無い表示でも、選手リンクを含む最小のデータ行から抽出する。"""
    name = clean(a.get_text(" "))
    if not name:
        return None
    node = a
    row_text = ""
    for _ in range(8):
        node = getattr(node, "parent", None)
        if node is None:
            break
        t = clean(node.get_text(" "))
        if (re.search(r"\b(GK|DF|MF|FW)\s*\d{1,3}\b", t)
                and re.search(r"(?:19|20)\d{2}/\d{1,2}/\d{1,2}", t)
                and re.search(r"\d{3}\s*/\s*\d{2,3}", t)):
            row_text = t
            break
    if not row_text or len(row_text) > 1200:
        return None
    pm = re.search(r"\b(GK|DF|MF|FW)\s*(\d{1,3})\b", row_text)
    bm = re.search(r"((?:19|20)\d{2}/\d{1,2}/\d{1,2})", row_text)
    hm = re.search(r"(\d{3})\s*/\s*(\d{2,3})", row_text)
    if not (pm and bm and hm):
        return None
    before_birth = row_text[:bm.start()].replace(name, " ").replace("HG", " ")
    before_birth = re.sub(r"\b(?:GK|DF|MF|FW)\s*\d{1,3}\b", " ", before_birth)
    birthplace = clean(before_birth).strip("| -") or None
    after_hw = row_text[hm.end():]
    stats = re.findall(r"(?<!\d)(-|\d+)(?!\d)", after_hw)
    appearances = parse_int(stats[0]) if len(stats) >= 1 and stats[0] != "-" else None
    goals = parse_int(stats[1]) if len(stats) >= 2 and stats[1] != "-" else None
    number = int(pm.group(2))
    return {
        "name": name, "position": pm.group(1), "number": number,
        "birthplace": birthplace, "birthdate": bm.group(1),
        "height_cm": int(hm.group(1)), "weight_kg": int(hm.group(2)),
        "appearances": appearances, "goals": goals,
        "source_url": absolute(a.get("href")) or source_url,
        "slug": unique_player_slug(club_slug, name, number),
    }


def roster_anchors(soup):
    """最初の「選手一覧」見出しから次の大見出しまでの選手リンクだけ返す。"""
    headings = [h for h in soup.find_all(["h2", "h3"]) if clean(h.get_text(" ")) == "選手一覧"]
    if headings:
        heading = headings[0]
        found = []
        for el in heading.find_all_next(["a", "h2", "h3"]):
            if el is not heading and el.name in {"h2", "h3"}:
                label = clean(el.get_text(" "))
                if label and label != "選手一覧":
                    break
            if el.name == "a" and re.search(r"^/player/\d+/?(?:[?#].*)?$", el.get("href") or ""):
                found.append(el)
        if found:
            return found
    return soup.find_all("a", href=re.compile(r"^/player/\d+/?(?:[?#].*)?$"))


def parse_players(html, club_slug, source_url):
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    seen_urls = set()

    for table in soup.find_all("table"):
        txt = clean(table.get_text(" "))
        if "出生地" not in txt or "生年月日" not in txt or "身長/体重" not in txt:
            continue
        for tr in table.find_all("tr"):
            p = player_from_cells(tr.find_all(["th", "td"]), club_slug, source_url)
            if p and p["source_url"] not in seen_urls:
                seen_urls.add(p["source_url"]); rows.append(p)

    if not rows:
        for a in roster_anchors(soup):
            href = absolute(a.get("href")).split("?")[0].split("#")[0]
            if href in seen_urls:
                continue
            p = player_from_anchor(a, club_slug, source_url)
            if p:
                seen_urls.add(href); rows.append(p)
    return rows


def parse_player_table(html, club_slug, source_url):
    return parse_players(html, club_slug, source_url)


def parse_stadium(html, source_url):
    soup = BeautifulSoup(html, "html.parser")
    lines = [clean(x) for x in soup.stripped_strings if clean(x)]
    joined = "\n".join(lines)
    name = None
    capacity = None
    address = None

    for i, line in enumerate(lines):
        if line == "ホームスタジアム":
            for cand in lines[i + 1:i + 7]:
                if (cand and len(cand) <= 100 and "入場可能数" not in cand
                        and cand not in {"監督", "試合日程をカレンダーに追加"}
                        and not cand.startswith("更新日")):
                    name = cand
                    break
            if name:
                break

    if not name:
        m = re.search(r"ホームスタジアム\s+(.{1,100}?)\s+入場可能数\s*[:：]\s*([\d,]+)\s*人", clean(" ".join(lines)))
        if m:
            name = clean(m.group(1))
            capacity = int(m.group(2).replace(",", ""))

    if name:
        cm = re.search(re.escape(name) + r"\s*\n\s*入場可能数\s*[:：]\s*([\d,]+)\s*人", joined)
        if cm:
            capacity = int(cm.group(1).replace(",", ""))
        addr_pat = re.compile(re.escape(name) + r"\s*\n\s*入場可能数\s*[:：]\s*[\d,]+\s*人\s*\n([^\n]{3,160})")
        for am in addr_pat.finditer(joined):
            cand = clean(am.group(1))
            if cand != "監督" and not cand.startswith("監督") and re.search(r"[都道府県]", cand):
                address = cand
                break

    if not name or len(name) > 160:
        name = None
    return {"name": name, "capacity": capacity, "address": address, "source_url": source_url}


def discover_official_urls():
    found = {}
    s = new_session()
    for league in ("j1", "j2", "j3"):
        r = s.get(f"{BASE}/{league}/club/", timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            label = clean(a.get_text(" "))
            m = re.search(r"/club/([^/?#]+)/?", a.get("href") or "")
            if label and m:
                found[label] = f"{BASE}/club/{m.group(1)}/player/"
        time.sleep(0.25)
    return found


def fetch_club(club_id, club_name, club_slug, fallback_url, official_url):
    url = official_url or fallback_url or f"{BASE}/club/{club_slug}/player/"
    s = new_session()
    r = s.get(url, timeout=22)
    r.raise_for_status()
    return {
        "club_id": club_id, "club_name": club_name, "club_slug": club_slug, "url": url,
        "players": parse_players(r.text, club_slug, url),
        "stadium": parse_stadium(r.text, url),
    }


def save_club(payload):
    with SessionLocal() as db:
        c = db.scalar(select(Club).where(Club.id == payload["club_id"]))
        if not c:
            return
        c.source_url = payload["url"]
        old = db.scalars(select(Player).where(Player.club_id == c.id)).all()
        for p in old:
            db.delete(p)
        db.flush()
        for pdata in payload["players"]:
            p = dict(pdata)
            base = p.pop("slug")[:155]
            unique = base
            i = 2
            while db.scalar(select(Player.id).where(Player.slug == unique)):
                suffix = f"-{i}"; unique = base[:160-len(suffix)] + suffix; i += 1
            db.add(Player(club_id=c.id, slug=unique, updated_at=datetime.now(timezone.utc), **p))

        stadium = payload["stadium"]
        if stadium.get("name"):
            s = db.scalar(select(Stadium).where(Stadium.name == stadium["name"]))
            if not s:
                st_slug = (slugify(stadium["name"], allow_unicode=False) or f"{c.slug}-stadium")[:175]
                s = Stadium(name=stadium["name"][:160], slug=st_slug,
                            capacity=stadium.get("capacity"), address=stadium.get("address"),
                            source_url=payload["url"])
                db.add(s); db.flush()
            else:
                s.capacity = stadium.get("capacity") or s.capacity
                s.address = stadium.get("address") or s.address
                s.source_url = payload["url"]
            c.stadium_id = s.id
        db.commit()


def main():
    init_db()
    try:
        official = discover_official_urls()
        print(f"official club URLs discovered: {len(official)}", flush=True)
    except Exception as e:
        print(f"WARN official URL discovery failed: {e}", flush=True)
        official = {}

    with SessionLocal() as db:
        clubs = db.scalars(select(Club).order_by(Club.league, Club.id)).all()
        club_rows = [(c.id, c.league, c.name, c.slug, c.source_url) for c in clubs]

    # 公式サイトへの同時アクセスは4件までに抑える。
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {
            pool.submit(fetch_club, cid, name, slug, fallback, official.get(name)):
            (league, name) for cid, league, name, slug, fallback in club_rows
        }
        for future in as_completed(jobs):
            league, name = jobs[future]
            try:
                payload = future.result()
                save_club(payload)
                print(f"{league} {name}: players={len(payload['players'])} stadium={payload['stadium'].get('name')}", flush=True)
            except Exception as e:
                print(f"ERROR {league} {name}: {e}", flush=True)

if __name__ == "__main__":
    if os.getenv("RUN_FULL_SYNC") != "1":
        print("Full J.League sync skipped (RUN_FULL_SYNC is not enabled).", flush=True)
        raise SystemExit(0)
    main()
