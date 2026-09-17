"""Shared official-source fan-page data for J1/J2/J3."""
import json
import gzip
import re
from pathlib import Path
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from fan_editorial import build_editorial, parse_comments
from lineup_stats import starting_rates, name_key

ROOT = Path(__file__).resolve().parent
BASE = 'https://www.jleague.jp'
OFFICIAL_SLUGS = {'kawasaki':'kawasakif','tokyo-v':'tokyov','yokohama-fm':'yokohamafm','c-osaka':'cosaka','g-osaka':'gosaka','tochigi-c':'tochigic','yokohama-fc':'yokohamafc','fc-osaka':'fosaka'}
LOCAL_SLUGS = {v:k for k,v in OFFICIAL_SLUGS.items()}


def soup_for(html):
    soup = BeautifulSoup(html, 'html.parser')
    for source,target in re.findall(r'\$RS\("(S:[^"\s]+)","(P:[^"\s]+)"\)',html):
        fragment,placeholder=soup.find(id=source),soup.find('template',id=target)
        if fragment is not None and placeholder is not None:
            for child in list(fragment.contents): placeholder.insert_before(child.extract())
            placeholder.decompose();fragment.decompose()
    return soup


def text(node):
    return node.get_text(' ',strip=True) if node else ''


def schedule(html, club_name):
    soup=soup_for(html);found={}
    for card in soup.select('.m-schedule'):
        a=card.select_one('a.m-schedule__link[href]')
        hit=re.search(r'/match/([a-z0-9]+)/(20\d{2})/(\d{4})\d{2}/',a['href']) if a else None
        if not hit:continue
        home=text(card.select_one('.m-schedule__team-home .m-schedule__team-name[data-media="pc"]'))
        away=text(card.select_one('.m-schedule__team-away .m-schedule__team-name[data-media="pc"]'))
        if name_key(club_name) not in [name_key(home),name_key(away)]:continue
        day=hit.group(2)+'-'+hit.group(3)[:2]+'-'+hit.group(3)[2:]
        scores=[text(p) for p in card.select('.m-schedule__score')]
        ended='試合終了' in text(card) and len(scores)==2 and all(x.isdigit() for x in scores)
        league=hit.group(1).upper()
        url=urljoin(BASE,a['href']).split('#')[0]
        kickoff=re.search(r'\d{1,2}:\d{2}',text(card.select_one('.m-schedule__status-time')))
        ticket=card.select_one('a[href*="jleague-ticket.jp"]')
        found[url]=dict(date=day,home=home,away=away,home_away='HOME' if name_key(home)==name_key(club_name) else 'AWAY',
            opponent=away if name_key(home)==name_key(club_name) else home,completed=ended,
            competition='明治安田'+league+'リーグ' if league in ['J1','J2','J3'] else 'ルヴァンカップ' if league=='LEAGUECUP' else league,
            round='',home_score=int(scores[0]) if ended else None,away_score=int(scores[1]) if ended else None,
            source_url=url,venue=text(card.select_one('.m-schedule__info-stadium[data-media="pc"]')),
            kickoff=kickoff.group() if kickoff else None,ticket_url=ticket['href'] if ticket else None)
    return list(found.values())


def news(html,checked_at):
    soup=soup_for(html);found={}
    for a in soup.select('a[href*="/news/article/"]'):
        title_node=a.select_one('h3,h2,[class*="title"]')
        title=text(title_node)
        raw=text(a)
        date=re.search(r'(20\d{2})/(\d{1,2})/(\d{1,2})',raw)
        if not title or not date:continue
        url=urljoin(BASE,a['href'])
        category=next((label for pattern,label in [('負傷|怪我|手術|復帰','負傷・復帰'),('移籍|加入|退団|契約','加入・移籍・契約'),('代表','代表'),('昇格|登録','若手・登録')] if re.search(pattern,title)),'クラブの動向')
        found[url]=dict(title=title,date=f'{int(date[1]):04}-{int(date[2]):02}-{int(date[3]):02}',url=url,category=category,summary='',checked_at=checked_at)
    return sorted(found.values(),key=lambda n:n['date'],reverse=True)[:6]


def lineup(html):
    soup=soup_for(html);teams={};report={}
    for side in ['home','away']:
        a=soup.select_one('a.o-page-header__club--'+side)
        if not a:raise ValueError('Club header missing')
        official=a['href'].rstrip('/').split('/')[-1]
        teams[side]=LOCAL_SLUGS.get(official,official)
        report[side]=dict(starters=[],goals=[],substitutions=[])
    for kind,selector in [('start','.p-game-details-lineup-tab__starting-members'),('bench','.p-game-details-lineup-tab__reserve-members')]:
        section=soup.select_one(selector)
        members=section.select_one('.m-lineup-list__members') if section else None
        if members is None:raise ValueError('Lineup table missing')
        cells=members.find_all(recursive=False)
        if kind=='start' and len(cells)!=22:raise ValueError('Expected 22 starting cells')
        for i,cell in enumerate(cells):
            name=text(cell.select_one('.m-lineup-list-item__name'))
            if not name:continue
            position=text(cell.select_one('.m-lineup-list-item__position'))
            number=re.search(r'\d+',position)
            if not number:raise ValueError('Shirt number missing')
            side='home' if i%2==0 else 'away'
            p=dict(name=name,number=int(number.group()))
            if kind=='start':report[side]['starters'].append(p)
            item=cell.select_one('.m-lineup-list-item')
            goals=int(item.get('goal',0)) if item else 0
            report[side]['goals'] += [dict(side=side,name=name,minute='—') for _ in range(goals)]
            badges=cell.select_one('.m-lineup-list-item__additional--pc')
            if badges:
                for badge in badges.select('.m-lineup-list__badge--sub'):
                    icon=badge.select_one('[variant]')
                    if icon is None:continue
                    minute=text(badge.select_one('p')).replace('’','')
                    direction=icon.get('variant')
                    if direction not in ['in','out']:continue
                    report[side]['substitutions'].append(dict(side=side,minute=minute,incoming=name if direction=='in' else '',outgoing=name if direction=='out' else ''))
    for side in report:
        if len(report[side]['starters'])!=11:raise ValueError('Incomplete team lineup')
    goals=report['home']['goals']+report['away']['goals']
    subs=report['home']['substitutions']+report['away']['substitutions']
    return {teams[side]:dict(starters=r['starters'],goals=goals,substitutions=subs,video=None) for side,r in report.items()}


def review(html,url,date,club_name):
    soup=soup_for(html)
    video=soup.select_one('youtube-video[src]')
    match=re.search(r'(?:youtu\.be/|youtube\.com/embed/)([\w-]{11})',video['src']) if video else None
    comments=parse_comments(str(soup),url,date,{},club_name=club_name)
    return dict(video=match.group(1) if match else None,comments=comments)


def build_club_hub(club,roster):
    path=ROOT/'data/club_content/league_clubs.json'
    snapshot=path.with_suffix('.json.gz')
    all_data=json.loads(path.read_text()) if path.exists() else json.loads(gzip.decompress(snapshot.read_bytes())) if snapshot.exists() else {}
    content=all_data.get(club.slug,dict(matches=[],checked_at='',team_updates=[]))
    starting=starting_rates(content,roster)
    ordered=sorted(roster,key=lambda p:(starting['players'][p.slug]['starts'] is None,-(starting['players'][p.slug]['starts'] or 0),p.number is None,p.number or 999))
    return dict(starting=starting,ordered_roster=ordered,editorial=build_editorial(content,roster),
                checked_at=content.get('checked_at','')[:16].replace('T',' '),team_name=club.name)
