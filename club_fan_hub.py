"""The same fan-guide context for every club, with official club-specific facts."""
import gzip
import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from club_hub import calendar_link, dated, JST
from league_content import build_club_hub, OFFICIAL_SLUGS
from lineup_stats import league_matches, name_key
ROOT=Path(__file__).resolve().parent

@lru_cache(maxsize=1)
def profiles():
    p=ROOT/'data/club_guides/clubs.json'
    return json.loads(p.read_text()) if p.exists() else {}

@lru_cache(maxsize=1)
def club_content():
    p=ROOT/'data/club_content/league_clubs.json'
    return json.loads(p.read_text()) if p.exists() else json.loads(gzip.decompress(p.with_suffix('.json.gz').read_bytes()))


def build_fan_hub(club,roster,standing,league_rows,fixtures,today=None):
    today=today or datetime.now(JST).date().isoformat()
    hub=build_club_hub(club,roster)
    content=club_content().get(club.slug,dict(matches=[],checked_at=''))
    profile=profiles().get(club.slug,{})
    official=profile.get('official_url',f'https://www.jleague.jp/club/{OFFICIAL_SLUGS.get(club.slug,club.slug)}/')
    links=profile.get('links',{})
    guide=dict(profile,official_url=official,guide_url=links.get('guide',{}).get('url',official),
               prices_url=links.get('tickets',{}).get('url',official),training_url=links.get('training',{}).get('url',official),
               checked_on=profile.get('checked_at','')[:10])
    teams={name_key(r['name']):r for r in league_rows}
    matches={}
    for m in content.get('matches',[]):
        if not m['completed'] and m['date']>=today:
            matches[m['source_url']]=dict(m,venue_full=m.get('venue',''),opponent_name=m['opponent'])
    # Include every available future fixture, beyond the current calendar month.
    for f in fixtures:
        home=f['home_club_id']==club.id
        url=f.get('match_url') or f.get('ticket_url')
        if not url:continue
        m=dict(date=str(f['match_date'])[:10],kickoff=f.get('kickoff'),home_away='HOME' if home else 'AWAY',
               opponent=f['away_name'] if home else f['home_name'],opponent_name=f['away_name'] if home else f['home_name'],
               competition=f.get('competition') or '明治安田'+club.league+'リーグ',round=f.get('round_label',''),
               venue_full=f.get('venue') or '',venue=f.get('venue') or '',source_url=url,ticket_url=f.get('ticket_url'),note='',completed=False)
        if m['date']>=today:matches[url]=m
    # Calendar and fixture sources can use different URL shapes for the same match.
    unique={}
    for m in matches.values():
        key=(m['date'],m['home_away'],name_key(m['opponent']))
        if key not in unique or m.get('ticket_url'):unique[key]=m
    upcoming=sorted(unique.values(),key=lambda m:(m['date'],m.get('kickoff') or '99:99'))
    for m in upcoming:
        m['date_label']=dated(m['date'])
        m['calendar']=calendar_link(m,content.get('checked_at') or datetime.now(JST).isoformat(),club.slug,club.name)
    results=[]
    for m in sorted(league_matches(content.get('matches',[])).values(),key=lambda m:m['date'],reverse=True)[:5]:
        home=m['home_away']=='HOME';ours=m['home_score'] if home else m['away_score'];theirs=m['away_score'] if home else m['home_score']
        if ours is None or theirs is None:continue
        outcome='win' if ours>theirs else 'loss' if ours<theirs else 'draw'
        results.append(dict(m,club_score=ours,opponent_score=theirs,outcome=outcome,label={'win':'勝','draw':'分','loss':'負'}[outcome],date_label=dated(m['date'])))
    form={k:sum(r['outcome']==k for r in results) for k in ['win','draw','loss']}
    form.update(gf=sum(r['club_score'] for r in results),ga=sum(r['opponent_score'] for r in results))
    rank=standing.get('rank') if standing else None
    next_match=upcoming[0] if upcoming else None
    comparison=teams.get(name_key(next_match['opponent'])) if next_match else None
    venue=dict(profile.get('venue',{}))
    if club.stadium:
        for key in ['name','address','capacity','access_note','gourmet_note','seat_note']:
            value=getattr(club.stadium,key,None)
            if value and not venue.get(key):venue[key]=value
    hub.update(guide=guide,profile=profile,venue=venue,news=profile.get('news') or content.get('team_updates',[]),
               matches=upcoming,next_match=next_match,comparison=comparison,form=form,league_results=results,
               nearby=[r for r in league_rows if rank and abs(r['rank']-rank)<=2],matchday=None,
               player_checked=max((str(p.updated_at)[:10] for p in roster if p.updated_at),default=''),
               standing_checked=str(standing.get('updated_at',''))[:10] if standing else '')
    return hub
