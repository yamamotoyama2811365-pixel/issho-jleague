"""Refresh every club's official fan content; cache completed match facts in CI."""
import ast
import json
import gzip
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from league_content import schedule,news,lineup,review,OFFICIAL_SLUGS
from lineup_stats import league_matches

OUT=ROOT/'data/club_content/league_clubs.json'
CACHE=ROOT/'.cache/league-match-facts'
CACHE.mkdir(parents=True,exist_ok=True)
NOW=datetime.now(timezone(timedelta(hours=9)))
CHECKED=NOW.isoformat(timespec='minutes')


def get(url):
    for attempt in range(3):
        try:
            r=requests.get(url,timeout=30,headers={'User-Agent':'IsshoJLeague/2.0 (official fan guide facts)'})
            r.raise_for_status()
            return r.text
        except requests.RequestException:
            if attempt==2:raise
            time.sleep(1)


def collect_club(item):
    league,name,slug=item
    matches={};recent_news=[]
    # The J.League club calendar is month-scoped. Fetch every month in the
    # current autumn/spring season so rate denominators never omit August.
    start_year=NOW.year if NOW.month>=8 else NOW.year-1
    year,month=start_year,8
    while (year,month)<=(NOW.year,NOW.month):
        html=get(f'https://www.jleague.jp/club/{OFFICIAL_SLUGS.get(slug,slug)}/day/?year={year}&month={month}')
        rows=schedule(html,name)
        for m in rows:
            if m['date']>=f'{start_year}-08-01':matches[m['source_url']]=m
        if (year,month)==(NOW.year,NOW.month):recent_news=news(html,CHECKED)
        month+=1
        if month==13:year,month=year+1,1
    if not matches:raise ValueError('No official calendar: '+slug)
    return slug,dict(name=name,league=league,checked_at=CHECKED,matches=sorted(matches.values(),key=lambda m:m['date']),
                     team_updates=recent_news,starting_lineups={},match_reports={},comments=[])


def collect_match(url,refresh=False):
    key=url.split('/match/')[1].strip('/').replace('/','-')
    cache=CACHE/(key+'.json')
    if cache.exists() and not refresh:
        return url,json.loads(cache.read_text())
    data=lineup(get(url+'lineup/'))
    cache.write_text(json.dumps(data,ensure_ascii=False))
    return url,data


def main():
    tree=ast.parse((ROOT/'app.py').read_text())
    clubs=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='CLUBS' for t in n.targets))
    items=[(league,name,slug) for league,rows in clubs.items() for name,slug in rows if slug!='sapporo']
    selected=os.getenv('CLUB_CONTENT_SLUGS')
    if selected:items=[x for x in items if x[2] in selected.split(',')]
    snapshot=OUT.with_suffix('.json.gz')
    previous=json.loads(OUT.read_text()) if OUT.exists() else json.loads(gzip.decompress(snapshot.read_bytes())) if snapshot.exists() else {}
    result=dict(previous);errors=[]
    with ThreadPoolExecutor(max_workers=6) as pool:
        pending={pool.submit(collect_club,item):item for item in items if not (os.getenv('REUSE_CALENDARS')=='1' and item[2] in result and result[item[2]].get('checked_at','').startswith(CHECKED[:10]))}
        for future in as_completed(pending):
            try:
                slug,data=future.result();result[slug]=data;OUT.write_text(json.dumps(result,ensure_ascii=False,separators=(',',':'))+'\n');print('calendar',slug,len(data['matches']),flush=True)
            except Exception as e:
                slug=pending[future][2];errors.append(slug);print('calendar unavailable',slug,type(e).__name__,flush=True)
    OUT.write_text(json.dumps(result,ensure_ascii=False,separators=(',',':'))+'\n')
    if any(item[2] not in result for item in items):raise RuntimeError('Initial calendar coverage incomplete: '+','.join(errors))
    urls={url for _,_,slug in items for url in league_matches(result[slug]['matches'])}
    latest_urls={max(league_matches(result[slug]['matches']).values(),key=lambda m:m['date'])['source_url'] for _,_,slug in items if league_matches(result[slug]['matches'])}
    # Seed immutable completed-match facts from the checked-in snapshot. The
    # latest fixture is always fetched again for corrections and late updates.
    seeded={}
    for slug,old in previous.items():
        for url,report in old.get('match_reports',{}).items():
            seeded.setdefault(url,{})[slug]=report
    for url,reports in seeded.items():
        key=url.split('/match/')[1].strip('/').replace('/','-')
        cached=CACHE/(key+'.json')
        if not cached.exists() and url not in latest_urls:
            cached.write_text(json.dumps(reports,ensure_ascii=False))
    facts={}
    with ThreadPoolExecutor(max_workers=6) as pool:
        pending={pool.submit(collect_match,url,url in latest_urls):url for url in urls}
        for future in as_completed(pending):
            try:
                url,data=future.result();facts[url]=data
                if len(facts)%25==0:print('match facts',len(facts),'/',len(urls),flush=True)
            except Exception as e:print('match unavailable',pending[future],type(e).__name__,flush=True)
    def assemble(item):
        _,name,slug=item;data=result[slug];data['recap_scope']='league'
        for m in data['matches']:
            if not m['completed']:continue
            url=m['source_url'];report=facts.get(url,{}).get(slug) or previous.get(slug,{}).get('match_reports',{}).get(url)
            if report:
                data['match_reports'][url]=report
                if url in league_matches(data['matches']):data['starting_lineups'][url]=report['starters']
        complete=sorted(league_matches(data['matches']).values(),key=lambda m:m['date'],reverse=True)
        if complete:
            m=complete[0];data['comments_match']=dict(date=m['date'],opponent=m['opponent'])
            try:
                url=m['source_url']+'review/'
                extra=review(get(url),url,m['date'],name)
                data['comments']=extra['comments']
                if m['source_url'] in data['match_reports']:data['match_reports'][m['source_url']]['video']=extra['video']
            except Exception as e:print('review unavailable',slug,type(e).__name__,flush=True)
        return slug,data
    with ThreadPoolExecutor(max_workers=6) as pool:
        for slug,data in pool.map(assemble,items):result[slug]=data
    OUT.write_text(json.dumps(result,ensure_ascii=False,separators=(',',':'))+'\n')
    print('Fan content clubs',len(result),'unique matches',len(facts),'of',len(urls),flush=True)


if __name__=='__main__':main()
