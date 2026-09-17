import unittest
from app import next_rounds_for
from scripts.sync_fixtures import parse_html


class FixtureTests(unittest.TestCase):
    def test_streamed_away_team_and_ticket_stay_with_their_match(self):
        clubs = [{"id": i, "name": n} for i, n in enumerate(["ホームA", "アウェイA", "ホームB", "アウェイB"], 1)]
        def card(code, home, away):
            return f'''<div class="m-schedule"><a class="m-schedule__link" href="/match/j1/2026/{code}/">
              <div class="m-schedule__team-home"><span class="m-schedule__team-name" data-media="pc">{home}</span></div>
              <p class="m-schedule__time-text">18:00</p>{away}
              <p class="m-schedule__info-stadium" data-media="pc">正しい会場</p></a></div>'''
        away = '<div class="m-schedule__team-away"><span class="m-schedule__team-name" data-media="pc">アウェイB</span></div>'
        html = '<div class="p-game-schedule__group"><div class="m-section-header"><h2>2026/9/19</h2>第8節</div>'
        html += card('091901', 'ホームA', '<template id="P:1"></template>')
        html += card('091902', 'ホームB', away).replace('</a></div>', '</a><a href="https://www.jleague-ticket.jp/match-b">購入</a></div>')
        html += '</div><div hidden id="S:1"><div class="m-schedule__team-away"><span class="m-schedule__team-name" data-media="pc">アウェイA</span></div></div><script>$RS("S:1","P:1")</script>'
        rows = parse_html(html, 'J1', clubs, [{"id": 1, "name": "正しい会場"}])
        self.assertEqual([(r['home_club_id'], r['away_club_id']) for r in rows], [(1, 2), (3, 4)])
        self.assertIsNone(rows[0]['ticket_url'])
        self.assertEqual(rows[1]['ticket_url'], 'https://www.jleague-ticket.jp/match-b')
        self.assertEqual(rows[0]['round_label'], '第8節')
        with self.assertRaises(ValueError):
            parse_html(html.replace('アウェイA', '不明なクラブ'), 'J1', clubs, [])

    def test_next_round_includes_all_days_without_fixed_limit(self):
        fixtures = [dict(league=league, round_label='第8節', match_date=f'2026-09-{19+i//5}', match_key=f'{league}-{i}') for league in ('J1','J2','J3') for i in range(10)]
        fixtures += [dict(league='J1', round_label='第9節', match_date='2026-09-26', match_key='later')]
        rounds = next_rounds_for(fixtures)
        self.assertEqual([len(rounds[league]['matches']) for league in ('J1','J2','J3')], [10, 10, 10])
        self.assertEqual(len(next_rounds_for([])['J1']['matches']), 0)
