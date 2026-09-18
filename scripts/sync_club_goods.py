"""Refresh official merchandise for all 60 clubs without a database connection."""
import ast
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from club_goods import STORE, JST, OFFICIAL_SLUGS, pokemon_catalog, parse_products, select_products

OUT = ROOT / 'data/club_content/club_goods.json'
NOW = datetime.now(JST)


def get(url):
    response = requests.get(url, timeout=25, headers={'User-Agent': 'IsshoJLeague/2.0 (official merchandise guide)'})
    response.raise_for_status()
    response.encoding = 'utf-8'
    return response.text


def collect(slug, catalog):
    official = OFFICIAL_SLUGS.get(slug, slug)
    entry = catalog.get(official)
    if not entry:
        raise ValueError('Official collaboration mapping missing')
    # A club-scoped Pokemon collection guarantees that other clubs cannot leak in.
    products = parse_products(get(entry['url']), official, entry['partner'])
    try:
        category = get(f'{STORE}/club/{official}/search/lcd-15/')
        products = parse_products(category, official, entry['partner']) + products
    except requests.RequestException:
        pass
    products = select_products(products)
    if not products:
        raise ValueError('No verified collaboration products')
    return {'checked_at': NOW.isoformat(timespec='minutes'), 'shop_url': f'{STORE}/club/{official}/',
            'collection_url': entry['url'], 'partner': entry['partner'], 'products': products}


def main():
    clubs = next(ast.literal_eval(n.value) for n in ast.parse((ROOT / 'app.py').read_text()).body
                 if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'CLUBS' for t in n.targets))
    slugs = [slug for rows in clubs.values() for _, slug in rows]
    previous = json.loads(OUT.read_text()) if OUT.exists() else {}
    pending = [s for s in slugs if s not in previous or NOW - datetime.fromisoformat(previous[s]['checked_at']) >= timedelta(hours=6)]
    if not pending:
        print('Official goods: all 60 clubs checked within 6 hours'); return
    try:
        catalog = pokemon_catalog(get(STORE + '/special/pokemon/'))
        if len(catalog) < 60:
            raise ValueError('Incomplete official directory')
    except (requests.RequestException, ValueError):
        if all(previous.get(s, {}).get('products') for s in slugs):
            print('Official catalog unavailable; preserving last verified data and timestamps'); return
        raise
    result = dict(previous)
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(collect, s, catalog): s for s in pending}
        for future in as_completed(futures):
            slug = futures[future]
            try:
                result[slug] = future.result()
                print('goods', slug, len(result[slug]['products']), flush=True)
            except Exception as error:
                print('goods unavailable', slug, type(error).__name__, flush=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    missing = [s for s in slugs if not result.get(s, {}).get('products')]
    if missing:
        raise RuntimeError('Missing initial merchandise: ' + ', '.join(missing))
    print('Official merchandise coverage: 60/60')


if __name__ == '__main__':
    main()
