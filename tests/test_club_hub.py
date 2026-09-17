import unittest
from urllib.parse import unquote
from club_hub import calendar_link, build_sapporo_hub
from scripts.sync_club_content import parse_schedule


class ClubHubTests(unittest.TestCase):
    def test_calendar_jst_conversion_utf8_folding_and_unconfirmed_dates(self):
        match = dict(date='2026-09-19', kickoff='14:00', note='', home_away='HOME',
                     opponent='大分トリニータ', venue_full='大和ハウス プレミストドーム',
                     source_url='https://www.consadole-sapporo.jp/game/info/20260919/')
        content = unquote(calendar_link(match, '2026-09-17T19:00+09:00').split(',', 1)[1])
        self.assertIn('DTSTART:20260919T050000Z\r\n', content)
        self.assertIn('札幌 vs 大分トリニータ', content.replace('\r\n ', ''))
        self.assertTrue(all(len(line.encode()) <= 75 for line in content.split('\r\n')))
        self.assertIsNone(calendar_link(dict(match, note='※9/19 or 9/20'), '2026-09-17T19:00+09:00'))
        self.assertIsNone(calendar_link(dict(match, kickoff=None), '2026-09-17T19:00+09:00'))

    def test_recent_form_excludes_cups_and_expires_matchday_event(self):
        results = [dict(competition='明治安田Ｊ２', club_score=1, opponent_score=1, match_date='2026-09-13')]
        results += [dict(competition='ルヴァンカップ', club_score=9, opponent_score=0, match_date='2026-09-12')]
        results += [dict(competition='明治安田Ｊ２リーグ', club_score=1, opponent_score=3, match_date='2026-09-06')]
        hub = build_sapporo_hub(None, results, [], [], today='2026-09-17')
        self.assertEqual(hub['form'], dict(win=0, draw=1, loss=1, gf=2, ga=4))
        self.assertIsNotNone(hub['matchday'])
        later = build_sapporo_hub(None, results, [], [], today='2026-09-20')
        self.assertIsNone(later['matchday'])
        self.assertTrue(all(m['date'] >= '2026-09-20' for m in later['matches']))

    def test_schedule_uses_visible_year_and_individual_card(self):
        cards = []
        for index in range(10):
            # Cup-style URLs deliberately contain no date; month heading is authoritative.
            cards.append(f'''<div class="relative @container"><p>明治安田Ｊ２リーグ</p><p>第{index+1}節</p>
              <span>2.{index+1}</span><span>14:00 K.O.</span>
              <span>home</span><b>札幌</b><span>-</span><span>away</span><b>相手{index}</b>
              <span>プレド</span><p>放送：DAZN</p>
              <a href="https://www.consadole-sapporo.jp/game/info/cup-{index}/">試合情報</a>
              <a href="https://www.jleague-ticket.jp/sales/{index}">チケット</a></div>''')
        matches = parse_schedule('<details><summary>2027.<span>2</span></summary>' + ''.join(cards) + '</details>')
        self.assertEqual(len(matches), 10)
        self.assertEqual(matches[0]['date'], '2027-02-01')
        self.assertEqual(matches[4]['opponent'], '相手4')
        self.assertEqual(matches[4]['ticket_url'], 'https://www.jleague-ticket.jp/sales/4')
        self.assertFalse(matches[4]['completed'])


if __name__ == '__main__':
    unittest.main()
