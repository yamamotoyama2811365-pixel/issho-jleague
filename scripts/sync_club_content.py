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
ORIGIN = 'https://www.consadole-sapporo.jp'
OUT = ROOT / 'data/club_content/sapporo.json'


def parse_news(html):
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
    return sorted(news, key=lambda n:n['date'], reverse=True)[:6]


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
        data['starting_lineups'] = {}
        for url, match in league_matches(data['matches']).items():
            try:
                response = session.get(url, timeout=30)
                response.raise_for_status()
                data['starting_lineups'][url] = parse_starters(response.text, match['home_away'])
            except (requests.RequestException, ValueError):
                if url in previous.get('starting_lineups', {}):
                    data['starting_lineups'][url] = previous['starting_lineups'][url]
                print(f'Lineup refresh unavailable: {match["date"]}; using previous record if available.')
        OUT.parent.mkdir(parents=True,exist_ok=True)
        OUT.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(f"Sapporo official content: news={len(data['news'])} matches={len(data['matches'])}")
    except (requests.RequestException,ValueError) as exc:
        if not OUT.exists():raise
        print(f'Official content refresh unavailable ({type(exc).__name__}); preserving dated snapshot.')


if __name__=='__main__':main()
