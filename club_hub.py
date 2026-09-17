"""Static-ready fan content for the Sapporo pilot, without browser API calls."""
import json
import re
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent
JST = timezone(timedelta(hours=9))


def calendar_link(match, checked_at):
    if not match.get('kickoff') or match.get('note'):
        return None
    try:
        start = datetime.fromisoformat(f"{match['date']}T{match['kickoff']}:00+09:00")
        stamp = datetime.fromisoformat(checked_at).astimezone(timezone.utc)
    except ValueError:
        return None
    def escaped(value):
        return str(value).replace('\\', '\\\\').replace('\n', '\\n').replace(',', '\\,').replace(';', '\\;')
    def fold(line):
        result, segment = [], ''
        for char in line:
            if len((segment + char).encode('utf-8')) > 75:
                result.append(segment)
                segment = ' '
            segment += char
        return '\r\n'.join(result + [segment])
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Issho JLeague//Sapporo//JA',
             'CALSCALE:GREGORIAN', 'BEGIN:VEVENT',
             f"UID:sapporo-{match['date']}-{match['home_away']}@issho-jleague.pages.dev",
             f"DTSTAMP:{stamp:%Y%m%dT%H%M%SZ}",
             f"DTSTART:{start.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}",
             'SUMMARY:' + escaped(f"札幌 vs {match['opponent']} ({match['home_away']})"),
             'LOCATION:' + escaped(match['venue_full']),
             'DESCRIPTION:' + escaped('日程変更・最新情報はクラブ公式で確認してください。'),
             'URL:' + match['source_url'], 'END:VEVENT', 'END:VCALENDAR']
    return 'data:text/calendar;charset=utf-8,' + quote('\r\n'.join(map(fold, lines)) + '\r\n', safe='')


def dated(value):
    value = str(value)
    date = datetime.fromisoformat(value[:10])
    return f"{date.month}/{date.day}（{'月火水木金土日'[date.weekday()]}）"


def build_sapporo_hub(standing, results, league_rows, roster, today=None):
    today = today or datetime.now(JST).date().isoformat()
    content = json.loads((ROOT / 'data/club_content/sapporo.json').read_text(encoding='utf-8'))
    guide = json.loads((ROOT / 'data/club_guides/sapporo.json').read_text(encoding='utf-8'))
    teams = {row['slug']: row for row in league_rows}
    matches = []
    for source in content['matches']:
        if source['completed'] or source['date'] < today:
            continue
        match = dict(source)
        match['date_label'] = dated(match['date'])
        match['venue_full'] = guide['venue_names'].get(match['venue'], match['venue'])
        match['opponent_slug'] = guide['club_slugs'].get(match['opponent'])
        opponent = teams.get(match['opponent_slug'])
        match['opponent_name'] = opponent['name'] if opponent else match['opponent']
        match['calendar'] = calendar_link(match, content['checked_at'])
        matches.append(match)
    league_results = []
    for source in results:
        competition = unicodedata.normalize('NFKC', source.get('competition') or '')
        if 'J2リーグ' not in competition or source.get('club_score') is None or source.get('opponent_score') is None:
            continue
        row = dict(source)
        difference = row['club_score'] - row['opponent_score']
        row['outcome'] = 'win' if difference > 0 else 'loss' if difference < 0 else 'draw'
        row['label'] = {'win':'勝', 'loss':'負', 'draw':'分'}[row['outcome']]
        row['date_label'] = dated(row['match_date'])
        league_results.append(row)
        if len(league_results) == 5:
            break
    form = {key:sum(r['outcome'] == key for r in league_results) for key in ['win','draw','loss']}
    form.update(gf=sum(r['club_score'] for r in league_results), ga=sum(r['opponent_score'] for r in league_results))
    rank = standing.get('rank') if standing else None
    nearby = [r for r in league_rows if rank and abs(r['rank'] - rank) <= 2]
    next_match = matches[0] if matches else None
    opponent = teams.get(next_match['opponent_slug']) if next_match else None
    # Compare league records only; cup opponents may belong to a different division.
    comparison = opponent if next_match and 'リーグ' in next_match['competition'] else None
    active_event = guide['matchday'] if any(m['date'] == guide['matchday']['date'] and m['home_away'] == 'HOME' for m in matches) else None
    newest_player = max((str(p.updated_at)[:10] for p in roster if p.updated_at), default='')
    return dict(guide=guide, news=content['news'], checked_at=content['checked_at'][:16].replace('T',' '),
                matches=matches, next_match=next_match, comparison=comparison, form=form,
                league_results=league_results, nearby=nearby, matchday=active_event,
                player_checked=newest_player,
                standing_checked=str(standing.get('updated_at',''))[:10] if standing else '')
