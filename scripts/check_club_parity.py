"""Prevent the pilot and other clubs from drifting apart again."""
from pathlib import Path
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parents[1]
REQUIRED={'next-match','team-updates','match-recap','match-comments','players-to-watch','team-form','club-schedule','club-news','club-goods','squad','home-guide','fan-messages','fan-ranking','club-voices'}

def check(html,slug):
    soup=BeautifulSoup(html,'html.parser')
    actual={e.get('id') for e in soup.select('section[id]')}
    assert REQUIRED<=actual,(slug,'missing content',REQUIRED-actual)
    assert soup.select_one('[data-fan-messages]')['data-fan-messages']==slug,(slug,'wrong message destination')
    assert soup.select_one('#home-guide iframe') or soup.select_one('#home-guide .dome-photo'),(slug,'missing venue media')
    for a in soup.select('.club-jump a[href^="#"]'):
        assert soup.find(id=a['href'][1:]),(slug,'broken section link',a['href'])
    assert soup.select('#club-goods .goods-product'),(slug,'missing goods')
    assert soup.select('#club-news .club-news-card'),(slug,'missing news')

if __name__=='__main__':
    pages=list((ROOT/'public/club').glob('*/index.html'))
    assert len(pages)==60,len(pages)
    for p in pages:check(p.read_text(),p.parent.name)
    print('All 60 clubs contain the same 14 fan sections, with club-specific message walls.')
