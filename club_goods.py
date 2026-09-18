"""Club-specific collaboration merchandise from official stores."""
import json
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from bs4 import BeautifulSoup
from league_content import OFFICIAL_SLUGS as LEAGUE_SLUGS

OFFICIAL_SLUGS = {**LEAGUE_SLUGS, "gunma": "kusatsu"}

ROOT = Path(__file__).resolve().parent
STORE = 'https://store.jleague.jp'
JST = timezone(timedelta(hours=9))


def pokemon_catalog(html):
    result = {}
    for a in BeautifulSoup(html, 'html.parser').select('a.pkm-pc-search__link[href]'):
        match = re.search(r'/club/([^/]+)/search/', a['href'])
        partner = a.select_one('.pkm-pc-search__pokemon')
        if match and partner:
            result[match[1]] = {'partner': partner.get_text(strip=True), 'url': a['href']}
    return result


def parse_products(html, official_slug, partner=''):
    found = {}
    for a in BeautifulSoup(html, 'html.parser').select('a.link-cmn-product-01[href]'):
        url = urlsplit(urljoin(STORE, a['href']))
        if url.hostname != 'store.jleague.jp' or not url.path.startswith(f'/club/{official_slug}/item/'):
            continue
        title, price = a.select_one('.txt-01'), a.select_one('.txt-price-01')
        if title is None or price is None:
            continue
        name = title.get_text(' ', strip=True)
        # The store's category also contains ordinary commemorative merchandise.
        # Only explicit collaborations or officially matched Pokemon are included.
        pokemon = any(word and word in name for word in ['ポケモン', 'ピカチュウ', partner.split('（')[0]])
        if not pokemon and not re.search(r'コラボ|collab|×|【[^】]+[|｜][^】]+】', name, re.I):
            continue
        canonical = STORE + url.path
        found[canonical] = {'title': name, 'price': price.get_text(' ', strip=True),
                            'url': canonical, 'group': 'ポケモン' if pokemon else 'コラボグッズ'}
    return list(found.values())


def select_products(products, limit=6):
    """Keep different collaborations and product types ahead of size/color variants."""
    unique = {p['url']: p for p in products}
    chosen, seen = [], set()
    for p in unique.values():
        kind = next((k for k in ['タオル', 'キーホルダー', 'キーリング', 'Tシャツ', 'Ｔシャツ', 'ユニフォーム', 'ステッカー', '缶バッジ'] if k in p['title']), p['title'])
        key = (p['group'], kind)
        if key not in seen:
            seen.add(key); chosen.append(p)
    return chosen[:limit]


@lru_cache(maxsize=1)
def snapshots():
    path = ROOT / 'data/club_content/club_goods.json'
    return json.loads(path.read_text()) if path.exists() else {}


def build_club_goods(slug, now=None):
    now = now or datetime.now(JST)
    data = dict(snapshots().get(slug, {}))
    official = OFFICIAL_SLUGS.get(slug, slug)
    data.setdefault('products', [])
    data.setdefault('shop_url', f'{STORE}/club/{official}/')
    checked = data.get('checked_at', '')
    data['checked_label'] = checked[:16].replace('T', ' ')
    data['stale'] = not checked or now - datetime.fromisoformat(checked) > timedelta(days=3)
    editorial = json.loads((ROOT / 'data/club_content/goods_editorial.json').read_text()).get(slug, [])
    data['features'] = [f for f in editorial if f['show_until'] >= now.date().isoformat()]
    return data
