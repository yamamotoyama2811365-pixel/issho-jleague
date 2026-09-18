import unittest
from datetime import datetime
from unittest.mock import patch
from club_goods import parse_products, select_products, build_club_goods, JST


def card(slug, code, title):
    return f'<a class="link-cmn-product-01" href="/club/{slug}/item/{code}/?tracking=1"><p class="txt-01">{title}</p><p class="txt-price-01">2,500円</p></a>'


class ClubGoodsTests(unittest.TestCase):
    def test_only_verified_collaborations_for_this_club(self):
        html = card('sapporo', '1', 'ヨルノズク タオル') + card('kashima', '2', 'ピカチュウ タオル') + card('sapporo', '3', '100試合記念 タオル') + card('sapporo', '4', 'キャラクター×クラブ キーホルダー') + card('sapporo', '1', 'ヨルノズク タオル')
        products = parse_products(html, 'sapporo', 'ヨルノズク')
        self.assertEqual(len(products), 2)
        self.assertTrue(all('/club/sapporo/' in p['url'] and '?' not in p['url'] for p in products))

    def test_choose_variety_before_variants(self):
        products = parse_products(card('sapporo', '1', 'ピカチュウ Tシャツ 白') + card('sapporo', '2', 'ピカチュウ Tシャツ 黒') + card('sapporo', '3', 'ピカチュウ タオル'), 'sapporo')
        self.assertIn('タオル', select_products(products, 2)[1]['title'])

    def test_expired_announcements_and_stale_snapshot(self):
        with patch('club_goods.snapshots', return_value={'sapporo': {'checked_at': '2026-09-18T12:00+09:00', 'products': []}}):
            data = build_club_goods('sapporo', datetime(2026, 10, 1, tzinfo=JST))
            self.assertEqual(data['features'], [])
            self.assertTrue(data['stale'])
            self.assertEqual(data['checked_label'], '2026-09-18 12:00')
