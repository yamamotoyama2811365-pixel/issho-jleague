import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import unquote
from club_fan_hub import build_fan_hub, profiles


class ClubFanHubTests(unittest.TestCase):
    def test_club_calendar_opponent_and_duplicate_sources(self):
        club=SimpleNamespace(id=9,slug='club-a',name='クラブＡ',league='J1',stadium=None)
        match=dict(date='2026-09-19',kickoff='19:00',home_away='HOME',opponent='クラブＢ',
                   source_url='https://example.com/game',venue='テスト会場',completed=False)
        fixture=dict(match_date='2026-09-19',kickoff='19:00',home_club_id=9,away_name='クラブB',
                     home_name='クラブＡ',match_url='https://example.com/other-source',ticket_url='https://example.com/ticket',venue='テスト会場')
        content={'club-a':dict(matches=[match],checked_at='2026-09-18T12:00+09:00')}
        standing=dict(rank=3,points=12)
        opponent=dict(name='クラブＢ',rank=4,points=11)
        with patch('club_fan_hub.build_club_hub',return_value={}),patch('club_fan_hub.club_content',return_value=content):
            hub=build_fan_hub(club,[],standing,[opponent],[fixture],today='2026-09-18')
        self.assertEqual(len(hub['matches']),1)
        self.assertEqual(hub['comparison']['points'],11)
        calendar=unquote(hub['next_match']['calendar'].split(',',1)[1]).replace('\r\n ','')
        self.assertIn('SUMMARY:クラブＡ vs クラブB',calendar)
        self.assertIn('UID:club-a-',calendar)
        self.assertIn('DTSTART:20260919T100000Z',calendar)
        self.assertNotIn('札幌',calendar)

    def test_every_profile_has_venue_and_verified_social_source(self):
        data=profiles()
        self.assertEqual(len(data),60)
        for slug,profile in data.items():
            self.assertTrue(profile['venue']['name'],slug)
            self.assertTrue(profile['venue']['address'],slug)
            self.assertTrue(profile['socials'],slug)
            self.assertTrue(all(s['source_url'] for s in profile['socials']),slug)
