"""Verified match facts and source-bound editorial summaries for the Sapporo pilot."""
import re
from collections import Counter
from bs4 import BeautifulSoup
from lineup_stats import parse_starters, name_key, league_matches


def parse_match_report(html, match):
    soup = BeautifulSoup(html, 'html.parser')
    starters = parse_starters(html, match['home_away'])
    def table_rows(label, side):
        heading = next((h for h in soup.find_all('h2') if h.get_text(strip=True) == label), None)
        table = heading.find_next_sibling('div', class_='stats-table') if heading else None
        box = table.find('div', class_='--'+side) if table else None
        if box is None:
            raise ValueError('Missing match fact table: '+label)
        return box.select('tbody tr')
    goals = []
    changes = []
    for side in ['home', 'away']:
        for row in table_rows('得点', side):
            cells = row.find_all('td')
            if len(cells) != 2:
                raise ValueError('Invalid scorer row')
            goals.append(dict(side=side, minute=cells[0].get_text(strip=True).replace(' ’',''), name=cells[1].get_text(strip=True)))
        for row in table_rows('交代', side):
            # Club markup names these CSS classes from the arrow's perspective:
            # out-pitch is the incoming substitute; in-pitch is the player leaving.
            incoming, outgoing = row.select_one('.out-pitch'), row.select_one('.in-pitch')
            if incoming is None or outgoing is None:
                raise ValueError('Invalid substitution row')
            changes.append(dict(side=side, minute=row.find('td').get_text(strip=True).replace(' ’',''),
                                incoming=incoming.get_text(strip=True), outgoing=outgoing.get_text(strip=True)))
    own = match['home_away'].lower()
    bench = []
    heading = next((h for h in soup.find_all('h3') if h.get_text(strip=True) == 'サブメンバー'), None)
    table = heading.find_next_sibling('div', class_='stats-table') if heading else None
    box = table.find('div', class_='--'+own) if table else None
    if box is None:
        raise ValueError('Bench missing')
    bench = [name_key(row.find_all('td')[2].get_text(strip=True)) for row in box.select('tbody tr')]
    # Validate direction against the actual named starting eleven and bench.
    field = {name_key(p['name']) for p in starters}
    for change in [c for c in changes if c['side'] == own]:
        into, out = name_key(change['incoming']), name_key(change['outgoing'])
        if out not in field or into in field or into not in bench:
            raise ValueError('Substitution does not match lineup')
        field.remove(out)
        field.add(into)
    videos = []
    for frame in soup.select('.game-info-report iframe[src]'):
        video = re.search(r'^https://www\.youtube\.com/embed/([\w-]{11})(?:[?/]|$)', frame['src'])
        if video:
            videos.append(video.group(1))
    return dict(starters=starters, goals=goals, substitutions=changes, video=videos[0] if videos else None)


def parse_comments(html, source_url, date, summaries, club_name='北海道コンサドーレ札幌'):
    soup = BeautifulSoup(html, 'html.parser')
    comments = []
    for group in soup.select('.p-game-details-highlight__comment-list'):
        if not name_key(group.get_text(' ', strip=True)).startswith(name_key(club_name)):
            continue
        for card in group.select('.player-comment'):
            name, body = card.select_one('.player-comment__name'), card.select_one('.player-comment__content-text')
            role = card.select_one('.player-comment__position')
            if name is None or body is None:
                continue
            person = name.get_text(strip=True)
            text = body.get_text(' ', strip=True)
            curated = summaries.get(source_url, {}).get(name_key(person))
            # Curated prose applies only to this exact match/person. Future matches
            # get a short, clearly labelled excerpt, never a fabricated summary.
            summary = curated if curated else text[:45] + ('…' if len(text)>45 else '')
            comments.append(dict(name=person, role=role.get_text(strip=True) if role else '',
                                 text=summary, kind='要約' if curated else '冒頭抜粋', date=date, source_url=source_url))
    return comments[:3]


def recent_player_form(content, roster):
    matches = sorted(league_matches(content['matches']).values(), key=lambda m:m['date'], reverse=True)[:5]
    reports = content.get('match_reports', {})
    players = []
    for player in roster:
        key = name_key(player.name)
        history = []
        for match in matches:
            report = reports.get(match['source_url'])
            if not report:
                history.append(dict(date=match['date'], opponent=match['opponent'], status='確認中', goals=None))
                continue
            own = match['home_away'].lower()
            start = any(p['number'] == player.number and name_key(p['name']) == key for p in report['starters'])
            sub = any(c['side'] == own and name_key(c['incoming']) == key for c in report['substitutions'])
            goals = sum(g['side']==own and name_key(g['name'])==key for g in report['goals'])
            history.append(dict(date=match['date'], opponent=match['opponent'], status='先発' if start else '途中' if sub else '出場なし', goals=goals))
        complete = bool(history) and all(h['status'] != '確認中' for h in history)
        players.append(dict(player=player, history=history, complete=complete,
                            starts=sum(h['status']=='先発' for h in history),
                            appearances=sum(h['status'] in ['先発','途中'] for h in history),
                            goals=sum(h['goals'] or 0 for h in history)))
    return players


def build_editorial(content, roster):
    reports = content.get('match_reports', {})
    completed = sorted(league_matches(content['matches']).values() if content.get('recap_scope') == 'league' else [m for m in content['matches'] if m['completed']], key=lambda m:m['date'], reverse=True)
    latest = completed[0] if completed else None
    recap = dict(latest, **reports[latest['source_url']]) if latest and latest['source_url'] in reports else None
    form = recent_player_form(content, roster)
    by_slug = {f['player'].slug:f for f in form}
    ready = [f for f in form if f['complete']]
    picks = []
    # One scorer, one established starter, one newly promoted starter; no injury inference.
    rankings = [sorted([f for f in ready if f['goals'] > 0], key=lambda f:(-f['goals'], -f['starts'], f['player'].number or 999)),
                sorted([f for f in ready if f['starts'] > 0], key=lambda f:(-f['starts'], f['player'].number or 999)),
                sorted([f for f in ready if f['history'] and f['history'][0]['status']=='先発'],
                       key=lambda f:(f['starts'], -f['goals'], f['player'].number or 999))]
    for ranking, reason in zip(rankings, ['直近5試合の得点から注目', '継続して先発に起用', '直近の試合で先発']):
        chosen = next((f for f in ranking if all(p['player'].slug != f['player'].slug for p in picks)), None)
        if chosen:
            picks.append(dict(chosen, reason=reason))
    return dict(recap_label='直近のリーグ戦' if content.get('recap_scope') == 'league' else '直近の公式戦', recap=recap, updates=content.get('team_updates', []), comments=content.get('comments', []),
                comments_match=content.get('comments_match'), form=by_slug, picks=picks,
                checked_at=content['checked_at'][:16].replace('T',' '))
