"""Notify participating search engines after verifying the published ownership file."""
import argparse
from pathlib import Path
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
import requests

SITE='https://issho-jleague.pages.dev'
ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--all',action='store_true')
    args=parser.parse_args()
    files=list((ROOT/'public_files').glob('*.txt'))
    key_file=next(p for p in files if p.stem==p.read_text().strip() and len(p.stem)==32)
    key=key_file.read_text().strip()
    location=SITE+'/'+key_file.name
    check=requests.get(location,timeout=30);check.raise_for_status()
    if check.text.strip()!=key:raise RuntimeError('Published ownership file mismatch')
    response=requests.get(SITE+'/sitemap.xml',timeout=30);response.raise_for_status()
    urls=[n.text for n in ET.fromstring(response.content).iter('{http://www.sitemaps.org/schemas/sitemap/0.9}loc')]
    if not args.all:
        urls=[u for u in urls if urlparse(u).path.startswith('/club/') or urlparse(u).path in ['/', '/schedule/', '/results/', '/standings/', '/clubs/']]
    if not urls or any(urlparse(u).netloc!=urlparse(SITE).netloc for u in urls):raise RuntimeError('Invalid sitemap URLs')
    response=requests.post('https://api.indexnow.org/indexnow',json=dict(host=urlparse(SITE).netloc,key=key,keyLocation=location,urlList=urls),timeout=60)
    print('IndexNow HTTP',response.status_code,'submitted URLs',len(urls))
    if response.status_code not in [200,202]:raise RuntimeError('Search notification failed')
    print('Received; inclusion in search results is decided by each search engine.')

if __name__=='__main__':main()
