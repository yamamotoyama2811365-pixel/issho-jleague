"""Refresh the Sapporo pilot's public news and full club schedule (two requests)."""
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lineup_stats import league_matches, parse_starters
from fan_editorial import parse_match_report, parse_comments
ORIGIN = 'https://www.consadole-sapporo.jp'
OUT = ROOT / 'data/club_content/sapporo.json'


def parse_news(html, limit=6):
    soup = BeautifulSoup(html, 'html.parser')
    news, seen = [], set()
    for anchor in soup.select('a[href]'):
        url = anchor['href']
        if not re.match(r'https://www\.consadole-sapporo\.jp/news/20\d{2}/\d{2}/[^/]+/$', url) or url in seen:
            continue
        card = anchor.parent
        title = card.find('h3')
        date = re.search(r'20\d{2}\.\d{2}\.\d{2}', card.get_text(' ', strip=True))
        if not title or not date:
            continue
        seen.add(url)
        tags = [n.get_text(strip=True) for n in card.select('span') if n.get_text(strip=True) in ['試合','イベント','チーム・選手','チケット','ファンクラブ','クラブ','グッズ']]
        news.append(dict(title=title.get_text(' ',strip=True), date=date.group().replace('.','-'), url=url, category=' / '.join(tags[:2]) or 'クラブ'))
    if not news:
        raise ValueError('Official news cards missing')
    return sorted(news, key=lambda n:n['date'], reverse=True)[:limit]


def parse_schedule(html):
    soup = BeautifulSoup(html, 'html.parser')
    matches, seen = [], set()
    for box in soup.find_all('div', class_=lambda value: value and '@container' in value.split()):
        info = box.select_one('a[href*="/game/info/"]')
        if not info or info['href'] in seen:
            continue
        parts = list(box.stripped_strings)
        if 'home' not in parts or 'away' not in parts:
            raise ValueError('Missing home/away labels')
        month_group = box.find_parent('details')
        month_heading = month_group.find('summary') if month_group else None
        year_month = re.search(r'(20\d{2})\.(\d{1,2})', month_heading.get_text('',strip=True) if month_heading else '')
        url_day = re.search(r'/game/info/(20\d{6})/', info['href'])
        # The visible month heading is authoritative; some cup URLs contain names.
        if year_month:
            year = int(year_month.group(1))
        elif url_day:
            year = int(url_day.group(1)[:4])
        else:
            raise ValueError('Missing schedule year')
        day = next((re.fullmatch(r'(\d{1,2})\.(\d{1,2})', p) for p in parts if re.fullmatch(r'\d{1,2}\.\d{1,2}', p)), None)
        if not day:
            raise ValueError('Missing match date')
        date = datetime(year, int(day.group(1)), int(day.group(2))).date().isoformat()
        home, away = parts[parts.index('home')+1], parts[parts.index('away')+1]
        if '札幌' not in (home,away):
            raise ValueError('Unexpected teams in Sapporo schedule')
        broadcast_index = next((i for i,p in enumerate(parts) if p.startswith('放送：')), None)
        kickoff = next((re.search(r'\d{1,2}:\d{2}', p).group() for p in parts if 'K.O.' in p and re.search(r'\d{1,2}:\d{2}',p)), None)
        note = next((p for p in parts if p.startswith('※') or '変更あり' in p), '')
        score_bits = parts[parts.index('home')+2:parts.index('away')]
        score = ''.join(score_bits)
        completed = bool(re.fullmatch(r'\d+-\d+',score))
        event = box.select_one('a[href*="/game_event/"]')
        ticket = box.select_one('a[href*="jleague-ticket.jp/"]')
        matches.append(dict(date=date, kickoff=kickoff, home=home, away=away,
            opponent=away if home=='札幌' else home, home_away='HOME' if home=='札幌' else 'AWAY',
            venue=parts[broadcast_index-1] if broadcast_index is not None else '',
            broadcast=parts[broadcast_index].replace('放送：','',1) if broadcast_index is not None else '',
            competition=parts[0], round=parts[1], note=note, completed=completed,
            home_score=int(score.split('-')[0]) if completed else None,
            away_score=int(score.split('-')[1]) if completed else None,
            source_url=info['href'], ticket_url=ticket['href'] if ticket else None,
            event_url=event['href'] if event else None))
        seen.add(info['href'])
    if len(matches)<10:
        raise ValueError('Official schedule coverage too small')
    return sorted(matches,key=lambda m:m['date'])


def main():
    session=requests.Session()
    session.headers['User-Agent']='IsshoJLeague/1.8 (Sapporo fan guide)'
    try:
        home=session.get(ORIGIN+'/',timeout=30);home.raise_for_status()
        schedule=session.get(ORIGIN+'/game/list/',timeout=30);schedule.raise_for_status()
        data=dict(checked_at=datetime.now(timezone(timedelta(hours=9))).isoformat(timespec='minutes'),news=parse_news(home.text),matches=parse_schedule(schedule.text))
        previous = json.loads(OUT.read_text(encoding='utf-8')) if OUT.exists() else {}
        editorial = json.loads((ROOT/'data/club_content/sapporo_editorial.json').read_text(encoding='utf-8'))
        data['starting_lineups'] = {}
        data['match_reports'] = {}
        for match in [m for m in data['matches'] if m['completed']]:
            url = match['source_url']
            try:
                response = session.get(url, timeout=30)
                response.raise_for_status()
                if url in league_matches(data['matches']):
                    data['starting_lineups'][url] = parse_starters(response.text, match['home_away'])
                data['match_reports'][url] = parse_match_report(response.text, match)
            except (requests.RequestException, ValueError):
                if url in previous.get('starting_lineups', {}):
                    data['starting_lineups'][url] = previous['starting_lineups'][url]
                if url in previous.get('match_reports', {}):
                    data['match_reports'][url] = previous['match_reports'][url]
                print(f'Lineup refresh unavailable: {match["date"]}; using previous record if available.')
        try:
            response = session.get(ORIGIN+'/news/?_c=46', timeout=30)
            response.raise_for_status()
            updates = []
            for news in parse_news(response.text, limit=None):
                title = news['title']
                category = next((label for pattern,label in [
                    ('負傷|怪我|手術|復帰|離脱','負傷・復帰'),
                    ('移籍|加入|退団|契約','加入・移籍・契約'),
                    ('代表.*選出|代表.*招集','代表選出'),
                    ('昇格|トップチーム登録|特別指定','若手・登録')]
                    if re.search(pattern, title)), None)
                if category:
                    updates.append(dict(news, category=category, summary=editorial['updates'].get(news['url'], ''), checked_at=data['checked_at']))
            data['team_updates'] = updates[:6]
        except (requests.RequestException, ValueError):
            data['team_updates'] = previous.get('team_updates', [])
        data['comments'] = []
        completed = sorted([m for m in data['matches'] if m['completed']], key=lambda m:m['date'], reverse=True)
        if completed:
            latest = completed[0]
            data['comments_match'] = dict(date=latest['date'], opponent=latest['opponent'])
            try:
                response = session.get('https://www.jleague.jp/club/sapporo/day/', timeout=30)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, 'html.parser')
                date = latest['date'].replace('-','')
                pattern = re.compile(r'^/match/[a-z0-9]+/'+date[:4]+'/'+date[4:]+r'\d{2}/$')
                link = next((a['href'] for a in soup.select('a[href]') if pattern.match(a['href'])), None)
                if link:
                    url = 'https://www.jleague.jp'+link+'review/'
                    response = session.get(url, timeout=30)
                    response.raise_for_status()
                    data['comments'] = parse_comments(response.text, url, latest['date'], editorial['comments'])
            except (requests.RequestException, ValueError):
                if previous.get('comments_match', {}).get('date') == latest['date']:
                    data['comments'] = previous.get('comments', [])
        OUT.parent.mkdir(parents=True,exist_ok=True)
        OUT.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(f"Sapporo official content: news={len(data['news'])} matches={len(data['matches'])}")
    except (requests.RequestException,ValueError) as exc:
        if not OUT.exists():raise
        print(f'Official content refresh unavailable ({type(exc).__name__}); preserving dated snapshot.')


if __name__=='__main__':main()
