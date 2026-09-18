import unittest
from pathlib import Path
from types import SimpleNamespace
from league_content import lineup, soup_for, schedule
from fan_editorial import recent_player_form
from lineup_stats import starting_rates

class OfficialLineupTests(unittest.TestCase):
    def test_future_calendar_keeps_kickoff_broadcast_and_cup_name(self):
        html='''<div class="m-schedule"><a class="m-schedule__link" href="/match/emperor/2026/092317/">
        <div class="m-schedule__team-home"><span class="m-schedule__team-name" data-media="pc">鹿島アントラーズ</span></div>
        <div class="m-schedule__team-away"><span class="m-schedule__team-name" data-media="pc">ヴァンフォーレ甲府</span></div>
        <p class="m-schedule__time-text">17:00</p><p class="m-schedule__info-platform">スカパー！</p>
        <p class="m-schedule__info-stadium" data-media="pc">メルカリスタジアム</p></a></div>'''
        match=schedule(html,'鹿島アントラーズ')[0]
        self.assertEqual((match['date'],match['kickoff'],match['competition'],match['broadcast']),
                         ('2026-09-23','17:00','天皇杯','スカパー！'))
        self.assertFalse(match['completed'])

    def setUp(self):
        self.html = (Path(__file__).parent/'fixtures/sendai-sapporo-lineup.html').read_text()
        self.facts = lineup(self.html)

    def test_teams_and_substitute_direction(self):
        self.assertEqual(set(self.facts), {'sendai', 'sapporo'})
        report = self.facts['sapporo']
        self.assertEqual(len(report['starters']), 11)
        self.assertIn('大森 真吾', [p['name'] for p in report['starters']])
        self.assertNotIn('青木 亮太', [p['name'] for p in report['starters']])
        self.assertTrue(any(c['incoming']=='青木 亮太' and c['side']=='away' for c in report['substitutions']))
        self.assertTrue(any(c['outgoing']=='ティラパット' and c['side']=='away' for c in report['substitutions']))

    def test_recent_appearance_and_goal_are_not_confused(self):
        match=dict(date='2026-09-13', completed=True, competition='明治安田J2リーグ',source_url='match',home_away='AWAY',opponent='仙台',round='6')
        report=self.facts['sapporo']
        content=dict(matches=[match],match_reports={'match':report}, starting_lineups={'match':report['starters']})
        starter=next(p for p in report['starters'] if p['name']=='大森 真吾')
        roster=[SimpleNamespace(name='大森 真吾',number=starter['number'],slug='omori'),SimpleNamespace(name='青木 亮太',number=11,slug='aoki'),SimpleNamespace(name='未出場選手',number=99,slug='unused')]
        forms=recent_player_form(content,roster)
        self.assertEqual([(f['starts'],f['appearances'],f['goals']) for f in forms],[(1,1,1),(0,1,0),(0,0,0)])
        content['matches'].append(dict(match,source_url='missing',date='2026-09-06'))
        rates=starting_rates(content,roster)
        self.assertFalse(rates['complete'])
        self.assertIsNone(rates['players']['omori']['percent'])

    def test_reject_incomplete_starting_eleven(self):
        soup=soup_for(self.html)
        soup.select_one('.p-game-details-lineup-tab__starting-members .m-lineup-list__members').find(recursive=False).decompose()
        with self.assertRaises(ValueError):lineup(str(soup))

if __name__=='__main__':unittest.main()
