"""2026/27シーズンのクラブページから、選手の基本事実とホームスタジアム事実を同期する。

重要:
- 画像、紹介文、ロゴ、エンブレム、公式の座席図は保存しない。
- 保存対象は氏名・背番号・ポジション・出身地・生年月日・身長体重・出場/得点・スタジアム名/収容人数/住所などの事実項目のみ。
- 各レコードには出典URLを残す。
- 実運用前にデータ利用条件・取得頻度を確認すること。
"""
import re
import time
import sys
from pathlib import Path
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup
from sqlalchemy import select
from slugify import slugify

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import SessionLocal, Club, Player, Stadium, init_db  # noqa: E402

UA = "IsshoJLeague/1.0 (+site admin; factual roster sync)"
HEADERS = {"User-Agent": UA, "Accept-Language": "ja,en;q=0.5"}


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def parse_int(text):
    m = re.search(r"\d[\d,]*", text or "")
    return int(m.group(0).replace(",", "")) if m else None


def parse_player_table(html, club_slug, source_url):
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    table = None
    for t in soup.find_all("table"):
        txt = clean(t.get_text(" "))
        if "出生地" in txt and "生年月日" in txt and "身長/体重" in txt:
            table = t
            break
    if not table:
        return rows
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th","td"])
        if len(cells) < 6:
            continue
        first = clean(cells[0].get_text(" "))
        if first == "選手" or "出生地" in first:
            continue
        first = first.replace("HG", " ")
        pm = re.search(r"\b(GK|DF|MF|FW)\s*(\d+)?", first)
        position = pm.group(1) if pm else None
        number = int(pm.group(2)) if pm and pm.group(2) else None
        a = cells[0].find("a", href=True)
        name = clean(a.get_text(" ")) if a else ""
        if not name:
            raw = first
            if pm:
                raw = clean((first[:pm.start()] + " " + first[pm.end():]).strip())
            name = raw
        if name and len(name) % 2 == 0:
            half = len(name)//2
            if name[:half] == name[half:]:
                name = name[:half]
        detail_url = source_url
        if a:
            href = a["href"]
            if href.startswith("/"):
                detail_url = "https://www.jleague.jp" + href
        birthplace = clean(cells[1].get_text(" ")) or None
        birthdate = clean(cells[2].get_text(" ")) or None
        hw = clean(cells[3].get_text(" "))
        h = w = None
        m = re.search(r"(\d{3})\s*/\s*(\d{2,3})", hw)
        if m:
            h, w = int(m.group(1)), int(m.group(2))
        app_text = clean(cells[4].get_text(" "))
        goal_text = clean(cells[5].get_text(" "))
        appearances = parse_int(app_text) if app_text != "-" else None
        goals = parse_int(goal_text) if goal_text != "-" else None
        rows.append({
            "name": name, "position": position, "number": number, "birthplace": birthplace,
            "birthdate": birthdate, "height_cm": h, "weight_kg": w,
            "appearances": appearances, "goals": goals, "source_url": detail_url,
            "slug": f"{club_slug}-{slugify(name, allow_unicode=False) or slugify(name, allow_unicode=True)}-{number or 'x'}"
        })
    return rows


def parse_stadium(html, source_url):
    soup = BeautifulSoup(html, "html.parser")
    text = clean(soup.get_text(" \n "))
    capacity = None
    address = None
    name = None
    m = re.search(r"ホームスタジアム\s+(.+?)\s+入場可能数[:：]\s*([\d,]+)人", text)
    if m:
        name = clean(m.group(1))
        capacity = int(m.group(2).replace(",", ""))
    am = re.search(r"〒\s*\d{3}-\d{4}\s+([^\n]+?)(?:\s+地図で見る|\s+マスコット|$)", text)
    if am:
        address = clean(am.group(0).split("地図で見る")[0])
    if not name:
        for h in soup.find_all(["h2","h3"]):
            if "スタジアム" in clean(h.get_text(" ")):
                nxt = h.find_next()
                while nxt and nxt.name not in ["h2","h3"]:
                    t = clean(nxt.get_text(" "))
                    if t and "入場可能数" not in t and not t.startswith("〒") and len(t) < 100:
                        name = t
                        break
                    nxt = nxt.find_next()
                break
    return {"name":name, "capacity":capacity, "address":address, "source_url":source_url}


def sync_club(club, delay=1.2):
    url = club.source_url or f"https://www.jleague.jp/club/{club.slug}/player/"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    players = parse_player_table(r.text, club.slug, url)
    stadium = parse_stadium(r.text, url)
    with SessionLocal() as db:
        c = db.scalar(select(Club).where(Club.id == club.id))
        if stadium.get("name"):
            s = db.scalar(select(Stadium).where(Stadium.name == stadium["name"]))
            if not s:
                s = Stadium(name=stadium["name"], slug=slugify(stadium["name"], allow_unicode=False) or club.slug+"-stadium")
                db.add(s)
                db.flush()
            s.capacity = stadium.get("capacity") or s.capacity
            s.address = stadium.get("address") or s.address
            s.source_url = url
            c.stadium_id = s.id
        old = db.scalars(select(Player).where(Player.club_id == c.id)).all()
        for p in old:
            db.delete(p)
        db.flush()
        for p in players:
            base = p.pop("slug")
            unique = base
            i = 2
            while db.scalar(select(Player.id).where(Player.slug == unique)):
                unique = f"{base}-{i}"; i += 1
            db.add(Player(club_id=c.id, slug=unique, updated_at=datetime.now(timezone.utc), **p))
        db.commit()
    print(f"{club.league} {club.name}: players={len(players)} stadium={stadium.get('name')}")
    time.sleep(delay)


def main():
    init_db()
    with SessionLocal() as db:
        clubs = db.scalars(select(Club).order_by(Club.league, Club.id)).all()
    for club in clubs:
        try:
            sync_club(club)
        except Exception as e:
            print(f"ERROR {club.name}: {e}")

if __name__ == "__main__":
    main()
