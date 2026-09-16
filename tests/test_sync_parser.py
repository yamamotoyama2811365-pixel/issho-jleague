import os
os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
from scripts.sync_jleague import parse_player_table

def test_parse_player_table():
    html='''<html><table><tr><th>選手</th><th>出生地※1</th><th>生年月日</th><th>身長/体重</th><th>出場試合数※2</th><th>ゴール数※3</th></tr>
    <tr><td><a href="/player/123/">田中 太郎</a> GK 1</td><td>北海道</td><td>2000/1/2</td><td>188 / 82</td><td>6</td><td>0</td></tr></table></html>'''
    rows=parse_player_table(html,'sample','https://www.jleague.jp/club/sample/player/')
    assert len(rows)==1
    assert rows[0]['name']=='田中 太郎'
    assert rows[0]['position']=='GK'
    assert rows[0]['number']==1
    assert rows[0]['height_cm']==188
    assert rows[0]['weight_kg']==82
