"""クラブ公式プロフィールで確認できた選手SNSだけを保存する。

検索結果から推測したアカウントは登録しない。
現時点では公式プロフィールにSNS欄を持つ北海道コンサドーレ札幌を
最初のアダプタとして実装し、同形式でクラブごとに拡張する。
"""
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import SessionLocal, Club, Player, PlayerSocial, init_db  # noqa: E402

HEADERS = {"User-Agent": "IsshoJLeague/1.1 (+verified player social sync)", "Accept-Language": "ja,en;q=0.5"}
SUPPORTED = {
    "x.com": "X", "twitter.com": "X", "www.twitter.com": "X",
    "instagram.com": "Instagram", "www.instagram.com": "Instagram",
    "tiktok.com": "TikTok", "www.tiktok.com": "TikTok",
    "youtube.com": "YouTube", "www.youtube.com": "YouTube",
}
# プロフィール下部などに混ざるクラブ共通SNS。選手個人SNSとしては保存しない。
BLOCKED_HANDLES = {
    "@consaofficial",
    "@hokkaido_consadole_sapporo",
    "@hokkaidoconsadolesapporo",
    "@user",  # youtube.com/user/consadolesapporotv
}


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


def handle_from(url):
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    if not path:
        return None
    parts = path.split("/")
    first = parts[0]
    if first == "user" and len(parts) > 1:
        first = parts[1]
    if first in {"intent", "share", "watch", "channel"}:
        return None
    return "@" + first.lstrip("@")


def social_links_under_sns_heading(soup):
    headings = [h for h in soup.find_all(["h2", "h3", "h4"]) if clean(h.get_text(" ")).upper() == "SNS"]
    if not headings:
        return []
    heading = headings[0]
    out = []
    seen = set()
    for el in heading.find_all_next(["a", "h2", "h3", "h4"]):
        if el is not heading and el.name in {"h2", "h3", "h4"}:
            label = clean(el.get_text(" "))
            if label and label.upper() != "SNS":
                break
        if el.name != "a":
            continue
        href = el.get("href") or ""
        if href.startswith("//"):
            href = "https:" + href
        host = urlparse(href).netloc.lower()
        platform = SUPPORTED.get(host)
        handle = handle_from(href)
        if platform and href not in seen and handle not in BLOCKED_HANDLES:
            seen.add(href)
            out.append((platform, href))
    return out


def save_verified(player_id, links, source_url):
    added = 0
    with SessionLocal() as db:
        for platform, url in links:
            handle = handle_from(url)
            if handle in BLOCKED_HANDLES:
                continue
            exists = db.scalar(select(PlayerSocial).where(
                PlayerSocial.player_id == player_id,
                PlayerSocial.platform == platform,
                PlayerSocial.url == url,
            ))
            if exists:
                exists.source_url = source_url
                continue
            db.add(PlayerSocial(
                player_id=player_id,
                platform=platform,
                url=url,
                handle=handle,
                source_url=source_url,
            ))
            added += 1
        db.commit()
    return added


def sync_consadole():
    base = "https://www.consadole-sapporo.jp"
    team_url = f"{base}/team/"
    s = http()
    r = s.get(team_url, timeout=25)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    with SessionLocal() as db:
        club = db.scalar(select(Club).where(Club.slug == "sapporo"))
        if not club:
            raise RuntimeError("sapporo club not found")
        players = db.scalars(select(Player).where(Player.club_id == club.id)).all()
        player_rows = [{"id": p.id, "name": p.name} for p in players]

    profile_urls = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a.get("href") or "")
        if not re.match(r"^https://www\.consadole-sapporo\.jp/team/[^/?#]+/?$", href):
            continue
        if href.rstrip("/") == team_url.rstrip("/") or href in seen:
            continue
        seen.add(href)
        profile_urls.append(href)

    total_links = 0
    matched_profiles = 0
    for url in profile_urls:
        pr = s.get(url, timeout=25)
        pr.raise_for_status()
        psoup = BeautifulSoup(pr.text, "html.parser")
        hay = norm(clean(psoup.get_text(" ")))
        candidates = []
        for row in player_rows:
            needle = norm(row["name"])
            pos = hay.find(needle)
            if pos >= 0:
                candidates.append((pos, -len(needle), row))
        if not candidates:
            continue
        candidates.sort(key=lambda x: (x[0], x[1]))
        player = candidates[0][2]
        links = social_links_under_sns_heading(psoup)
        if not links:
            continue
        matched_profiles += 1
        total_links += save_verified(player["id"], links, url)
        print(f"札幌 {player['name']}: verified_personal_socials={len(links)}", flush=True)

    print(
        f"Consadole profiles={len(profile_urls)} matched_with_personal_social={matched_profiles} new_links={total_links}",
        flush=True,
    )
    return matched_profiles


def main():
    init_db()
    matched = sync_consadole()
    if matched == 0:
        raise SystemExit("verified social sync found no profiles; adapter needs review")


if __name__ == "__main__":
    main()
