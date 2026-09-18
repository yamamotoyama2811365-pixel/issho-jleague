"""Collect verified club links, stadium facts, news and social accounts."""
import ast
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit
import requests
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from league_content import OFFICIAL_SLUGS,soup_for,news
OUT=ROOT/'data/club_guides/clubs.json'
VERIFIED=json.loads((ROOT/'data/club_guides/verified_links.json').read_text())
CACHE=ROOT/'.cache/club-profiles';CACHE.mkdir(parents=True,exist_ok=True)
NOW=datetime.now(timezone(timedelta(hours=9)))
CHECKED=NOW.isoformat(timespec='minutes')
SOCIAL={'instagram.com':'Instagram','x.com':'X','twitter.com':'X','youtube.com':'YouTube','youtu.be':'YouTube','tiktok.com':'TikTok','facebook.com':'Facebook'}


def get(url,key):
    cache=CACHE/(key+'.html')
    if cache.exists() and datetime.fromtimestamp(cache.stat().st_mtime,timezone.utc)>NOW-timedelta(hours=6):return cache.read_text()
    url=url.replace('http://','https://',1)
    r=requests.get(url,timeout=22,headers={'User-Agent':'IsshoJLeague/2.0 (+official club fan guide)'})
    r.raise_for_status();r.encoding='utf-8' if 'jleague.jp' in url or 'utf-8' in r.headers.get('content-type','').lower() or b'utf-8' in r.content[:3000].lower() else r.apparent_encoding
    cache.write_text(r.text);return r.text


def parse_home(html,base):
    soup=BeautifulSoup(html,'html.parser');links={};social={};items={}
    site_host=urlsplit(base).hostname.replace('www.','')
    for a in soup.select('a[href]'):
        url=urljoin(base,a['href']);p=urlsplit(url)
        if p.scheme not in ['https','http']:continue
        label=a.get_text(' ',strip=True)
        if not label:
            img=a.select_one('img[alt]');label=img.get('alt','') if img else a.get('aria-label','')
        host=(p.hostname or '').replace('www.','')
        if host in SOCIAL and p.path.strip('/') and not re.search(r'/(?:intent|share|sharer|watch|embed|status|p|reel)(?:/|$)',p.path):
            # Club-owned navigation/footer accounts; never infer an account from a name.
            if a.find_parent(['footer','header','nav']) or any(re.search('sns|social|footer|header',(' '.join(x.get('class',[]))+' '+str(x.get('id',''))).lower()) for x in [a,*list(a.parents)] if hasattr(x,'get')):
                platform=SOCIAL[host]
                social.setdefault(platform,dict(platform=platform,url=url,source_url=base))
        if host!=site_host:continue
        if not label or len(label)>250:continue
        for key,pattern in [('guide','観戦ガイド|初めて|はじめて|観戦ルール'),('tickets','チケット|席種|料金'),('access','アクセス'),('food','グルメ|スタグル'),('events','イベント'),('training','練習|スケジュール'),('news','ニュース|NEWS'),('academy','アカデミー|育成'),('fanclub','ファンクラブ|後援会'),('stadium','スタジアム|ホームタウン')]:
            if re.search(pattern,label,re.I) and len(label)<60 and p.path not in ['', '/']:
                links.setdefault(key,dict(label=label,url=url))
        # Article titles with a nearby explicit publication date, not navigation links.
        date=re.search(r'(20\d{2})[./年-](\d{1,2})[./月-](\d{1,2})',label)
        date=date or re.search(r'/(20\d{2})/?(\d{2})/?(\d{2})(?:/|_|-|\.)',p.path)
        if not date and ('news' in p.path or 'article' in p.path):
            parent=a.parent
            if len(parent.get_text(' ',strip=True))<600:
                date=re.search(r'(20\d{2})[./年-](\d{1,2})[./月-](\d{1,2})',parent.get_text(' ',strip=True))
        title=re.sub(r'20\d{2}[./年-]\d{1,2}[./月-]\d{1,2}日?','',label).strip()
        if date and 12<=len(title)<=200:
            try:day=datetime(int(date[1]),int(date[2]),int(date[3])).date().isoformat()
            except ValueError:continue
            if day<=NOW.date().isoformat():
                items[url]=dict(title=title,date=day,url=url,category='クラブ公式',checked_at=CHECKED)
    return dict(links=links,socials=list(social.values()),news=sorted(items.values(),key=lambda n:n['date'],reverse=True)[:9])


def collect(item):
    league,name,slug=item;official=OFFICIAL_SLUGS.get(slug,slug)
    source=f'https://www.jleague.jp/club/{official}/'
    html=get(source,slug+'-league');s=soup_for(html)
    official_link=next((a for a in s.select('a[href]') if a.get_text(' ',strip=True)=='クラブ公式サイト'),None)
    if official_link is None:raise ValueError('Official club link missing')
    site=VERIFIED.get(slug,{}).get('official_url',official_link['href'].replace('http://','https://',1))
    venue={};h=s.find(id='stadium')
    if h:
        block=h.parent.parent
        title=block.select_one('.m-info-widget__title')
        paragraphs=[p.get_text(' ',strip=True) for p in block.select('.m-info-widget__description')]
        capacity=next((re.search(r'([\d,]+)人',t).group(1).replace(',','') for t in paragraphs if re.search(r'([\d,]+)人',t)),None)
        address=next((t for t in paragraphs if re.search('[都道府県]',t) and '入場可能数' not in t),'')
        venue=dict(name=title.get_text(' ',strip=True) if title else '',capacity=int(capacity) if capacity else None,address=address,source_url=source+'#stadium')
    details=dict(links={},socials=[],news=[])
    try:details=parse_home(get(site,slug+'-official'),site)
    except requests.RequestException as e:print('club website unavailable',slug,type(e).__name__,flush=True)
    if not details['news']:details['news']=news(html,CHECKED)
    details.update(name=name,league=league,slug=slug,official_url=site,source_url=source,venue=venue,checked_at=CHECKED)
    # Confirmed first-party links also cover sites whose menus are client-rendered.
    known=VERIFIED.get(slug,{}).get('socials',[])
    if known:
        details['socials']=list({s['platform']:s for s in [*details['socials'],*known]}.values())
    return slug,details


def main():
    clubs=next(ast.literal_eval(n.value) for n in ast.parse((ROOT/'app.py').read_text()).body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='CLUBS' for t in n.targets))
    items=[(league,name,slug) for league,rows in clubs.items() for name,slug in rows]
    result=json.loads(OUT.read_text()) if OUT.exists() else {}
    pending=[item for item in items if item[2] not in result or NOW-datetime.fromisoformat(result[item[2]]['checked_at'])>=timedelta(hours=18)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures={pool.submit(collect,item):item for item in pending}
        for f in as_completed(futures):
            try:
                slug,d=f.result()
                old=result.get(slug,{})
                if not d['socials']:d['socials']=old.get('socials',[])
                if not d['links']:d['links']=old.get('links',{})
                result[slug]=d;OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
                print('profile',slug,'news',len(d['news']),'social',len(d['socials']),'venue',bool(d['venue'].get('address')),flush=True)
            except Exception as e:print('profile unavailable',futures[f][2],type(e).__name__,flush=True)
    missing=[s for _,_,s in items if s not in result]
    if missing:raise RuntimeError('Missing profiles: '+','.join(missing))
    print('Club profiles: 60/60')


if __name__=='__main__':main()
