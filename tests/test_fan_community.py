import tempfile
import time
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
from flask import Flask
from sqlalchemy import create_engine, select, update
from fan_community import register, views, messages, limits, rankings, cleanup, initialize

CLUBS={'J1':[('A','a'),('B','b'),('C','c')],'J2':[('札幌','sapporo')],'J3':[('D','d')]}
HEADERS={'Origin':'https://issho-jleague.pages.dev','User-Agent':'Mozilla/5.0'}

class FanCommunityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.engine=create_engine('sqlite:///'+str(Path(self.tmp.name)/'fan.db'))
        self.app=Flask(__name__);self.app.testing=True;register(self.app,self.engine,CLUBS);self.client=self.app.test_client()
    def tearDown(self):self.engine.dispose();self.tmp.cleanup()
    def post(self,path,body,**kwargs):return self.client.post('/api/fan/'+path,json=body,headers=HEADERS,**kwargs)
    def submit(self,body='最後まで一緒に戦おう！'):
        return self.post('messages/sapporo',{'nickname':'赤黒ファン','body':body,'agree':True})
    def test_pending_never_public_and_owner_delete_is_authenticated(self):
        r=self.submit('あ'*20);self.assertEqual(r.status_code,201);receipt=r.get_json()
        self.assertEqual(self.client.get('/api/fan/messages/sapporo').get_json()['messages'],[])
        with self.engine.connect() as conn:
            row=conn.execute(select(messages)).mappings().one();self.assertEqual(row['status'],'pending');self.assertNotEqual(row['delete_hash'],receipt['delete_token'])
        self.assertEqual(self.post('messages/'+receipt['id']+'/delete',{'delete_token':'wrong'}).status_code,403)
        self.assertEqual(self.post('messages/'+receipt['id']+'/delete',{'delete_token':receipt['delete_token']}).status_code,200)
        with self.engine.connect() as conn:self.assertIsNone(conn.execute(select(messages)).first())
    def test_server_validation_and_origin_boundary(self):
        for body in ['あ'*21,'<script>','https://x.com','がんばれ\n札幌',{'bad':'type'}]:self.assertEqual(self.submit(body).status_code,400)
        self.assertEqual(self.client.post('/api/fan/messages/sapporo',json={'nickname':'x','body':'hi','agree':True}).status_code,403)
        self.assertEqual(self.post('view',{'club':[]}).status_code,400)
        self.assertEqual(self.post('messages/id/report',{'reason':[]}).status_code,400)
        r=self.client.options('/api/fan/messages/sapporo',headers=HEADERS);self.assertEqual(r.headers['Access-Control-Allow-Origin'],HEADERS['Origin'])
        bad=self.client.options('/api/fan/messages/sapporo',headers={'Origin':'https://evil.example'});self.assertNotIn('Access-Control-Allow-Origin',bad.headers)
    def test_rate_limit_and_data_persists(self):
        self.assertEqual(self.submit().status_code,201);self.assertEqual(self.submit().status_code,429)
        self.assertEqual(initialize(self.engine),initialize(self.engine))
        with self.engine.connect() as conn:self.assertEqual(len(conn.execute(select(messages)).all()),1)
    def test_reviewed_only_report_immediately_hides(self):
        receipt=self.submit().get_json()
        with self.engine.begin() as conn:conn.execute(update(messages).where(messages.c.id==receipt['id']).values(status='approved',reviewed_at=int(time.time())))
        public=self.client.get('/api/fan/messages/sapporo').get_json()['messages'];self.assertEqual(len(public),1);self.assertNotIn('delete_hash',public[0])
        self.assertEqual(self.post('messages/'+receipt['id']+'/report',{'reason':'personal'}).status_code,200)
        self.assertEqual(self.client.get('/api/fan/messages/sapporo').get_json()['messages'],[])
    def test_view_deduplication_and_rolling_ranks_without_counts(self):
        with patch('fan_community.time.time',return_value=1800000000):
            self.post('view',{'club':'a'});self.post('view',{'club':'a'})
        with patch('fan_community.time.time',return_value=1800000010):self.post('view',{'club':'b'})
        with patch('fan_community.time.time',return_value=1800000020):self.post('view',{'club':'a'})
        with self.engine.connect() as conn:self.assertEqual(len(conn.execute(select(views)).all()),2)
        data=rankings(self.engine,CLUBS,now=1800000030);rows=data['leagues']['J1'];self.assertEqual([r['rank'] for r in rows],[1,1,None]);self.assertTrue(rows[0]['tied']);self.assertFalse(rows[2]['tied'])
        self.assertFalse(any('count' in r or 'views' in r for r in rows))
        self.assertTrue(all(r['rank'] is None for r in rankings(self.engine,CLUBS,now=1800000030+8*86400)['leagues']['J1']))
    def test_view_source_and_daily_traffic(self):
        now=int(time.time())
        day=datetime.fromtimestamp(now,timezone(timedelta(hours=9))).date().isoformat()
        with patch('fan_community.time.time',return_value=now):
            self.post('view',{'club':'a','source':'x'})
        with self.engine.connect() as conn:
            row=conn.execute(select(views)).mappings().one()
            self.assertEqual(row['source'],'x')
            self.assertEqual(row['path'],'/club/a/')
        data=self.client.get('/api/fan/traffic?day='+day).get_json()
        self.assertEqual(data['pageviews'],1)
        self.assertEqual(data['sources'],{'x':1})
        self.assertEqual(data['top_pages'][0],{'path':'/club/a/','views':1})

    def test_cleanup_only_feature_retention(self):
        self.submit()
        with self.engine.begin() as conn:
            conn.execute(update(messages).values(created_at=1000))
            conn.execute(views.insert().values(id='old',club='a',created_at=1000))
        cleanup(self.engine,now=int(time.time()))
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(select(messages)).all(),[]);self.assertEqual(conn.execute(select(views)).all(),[])

    def test_all_clubs_accept_messages_and_public_walls_are_isolated(self):
        self.assertEqual(set(self.client.get('/api/fan/status').get_json()['message_clubs']), {'a','b','c','d','sapporo'})
        r=self.post('messages/a',{'nickname':'サポーター','body':'最後まで応援します','agree':True})
        self.assertEqual(r.status_code,201)
        self.assertEqual(self.client.get('/api/fan/messages/a').get_json()['messages'],[])
        with self.engine.begin() as conn:
            conn.execute(update(messages).where(messages.c.id==r.get_json()['id']).values(status='approved'))
        self.assertEqual(len(self.client.get('/api/fan/messages/a').get_json()['messages']),1)
        self.assertEqual(self.client.get('/api/fan/messages/b').get_json()['messages'],[])
        self.assertEqual(self.client.get('/api/fan/messages/sapporo').get_json()['messages'],[])
        self.assertEqual(self.post('messages/unknown',{'nickname':'x','body':'応援','agree':True}).status_code,404)
        self.assertEqual(self.client.get('/api/fan/messages/unknown').status_code,404)
