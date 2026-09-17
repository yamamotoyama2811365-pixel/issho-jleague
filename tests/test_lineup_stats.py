import unittest
from types import SimpleNamespace
from lineup_stats import starting_rates, parse_starters


class StartingRateTests(unittest.TestCase):
    def test_team_games_denominator_and_missing_records(self):
        matches = [dict(source_url=str(i), completed=True, competition='明治安田Ｊ２リーグ',
                        date=f'2026-08-{i+10}', round=f'第{i+1}節') for i in range(2)]
        matches += [dict(source_url='cup', completed=True, competition='天皇杯'),
                    dict(source_url='future', completed=False, competition='明治安田Ｊ２リーグ')]
        first = [dict(number=i, name=f'選手 {i}') for i in range(1,12)]
        second = [dict(number=i, name=f'選手 {i}') for i in range(2,13)]
        content = dict(matches=matches, starting_lineups={'0':first, '1':second})
        roster = [SimpleNamespace(number=1, name='選手　1', slug='one'),
                  SimpleNamespace(number=99, name='未出場', slug='zero')]
        rate = starting_rates(content, roster)
        self.assertEqual(rate['players']['one']['percent'], 50)
        self.assertEqual(rate['players']['zero']['percent'], 0)
        del content['starting_lineups']['1']
        self.assertIsNone(starting_rates(content, roster)['players']['one']['percent'])

    def test_empty_season(self):
        p = SimpleNamespace(number=1, name='選手', slug='one')
        self.assertIsNone(starting_rates({'matches':[]}, [p])['players']['one']['percent'])

    def test_parser_selects_sapporo_starters_only(self):
        def rows(start):
            return ''.join(f'<tr><td>{i}</td><td>MF</td><td>選手{i}</td></tr>' for i in range(start,start+11))
        html = '<h2>スターティングメンバー</h2><div class="stats-table">'
        html += '<div class="--home --238"><table><tbody>'+rows(20)+'</tbody></table></div>'
        html += '<div class="--away --276"><table><tbody>'+rows(1)+'</tbody></table></div></div>'
        html += '<h3>サブメンバー</h3><div class="stats-table">'+rows(70)+'</div>'
        self.assertEqual([p['number'] for p in parse_starters(html, 'AWAY')], list(range(1,12)))
        with self.assertRaises(ValueError):
            parse_starters(html, 'HOME')
        with self.assertRaises(ValueError):
            parse_starters(html.replace('<tr><td>1</td><td>MF</td><td>選手1</td></tr>', ''), 'AWAY')
