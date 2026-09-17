"""Observed starting rates, scoped to completed current-season J2 fixtures."""
import re
import unicodedata
from bs4 import BeautifulSoup


def name_key(name):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', name))


def league_matches(matches):
    return {m['source_url']: m for m in matches if m['completed'] and
            re.fullmatch(r'明治安田J[123]リーグ', unicodedata.normalize('NFKC', m['competition']))}


def parse_starters(html, home_away):
    soup = BeautifulSoup(html, 'html.parser')
    heading = next((h for h in soup.find_all('h2') if h.get_text(strip=True) == 'スターティングメンバー'), None)
    if heading is None:
        raise ValueError('Starting lineup missing')
    table = heading.find_next_sibling('div', class_='stats-table')
    side = table.find('div', class_='--' + home_away.lower()) if table else None
    if side is None or '--276' not in side.get('class', []):
        raise ValueError('Sapporo team identity missing')
    players = []
    for row in side.select('tbody tr'):
        cells = row.find_all('td')
        if len(cells) != 3 or not cells[0].get_text(strip=True).isdigit():
            raise ValueError('Invalid starting lineup row')
        players.append({'number': int(cells[0].get_text(strip=True)), 'name': cells[2].get_text(strip=True)})
    if len(players) != 11 or len({p['number'] for p in players}) != 11:
        raise ValueError('Starting lineup must contain eleven unique players')
    return players


def starting_rates(content, roster):
    matches = league_matches(content['matches'])
    records = content.get('starting_lineups', {})
    covered = {url: records[url] for url in matches if url in records and len(records[url]) == 11}
    total = len(matches)
    complete = total > 0 and len(covered) == total
    stats = {}
    for player in roster:
        count = sum(any(p['number'] == player.number and name_key(p['name']) == name_key(player.name)
                        for p in lineup) for lineup in covered.values())
        stats[player.slug] = dict(starts=count if complete else None, total=total,
                                 percent=(count * 100 + total // 2) // total if complete else None)
    return dict(players=stats, total=total, covered=len(covered), complete=complete,
                since=min((m['date'] for m in matches.values()), default=''),
                through=max((m['date'] for m in matches.values()), default=''),
                sources=[dict(url=url, date=m['date'], round=m['round']) for url,m in matches.items()])
