"""netkeiba パーサーのテスト（db.netkeiba.com の HTML 構造を模したフィクスチャを使用）。"""

import pytest

pytest.importorskip("bs4")

from keiba.scrape.netkeiba import (  # noqa: E402
    detect_class,
    parse_horse_page,
    parse_race_ids,
    parse_race_page,
    parse_time,
)

RACE_HTML = """
<html><body>
<div class="data_intro">
<dl class="racedata fc"><dd>
<h1>サンプルステークス</h1>
<p><span>芝右1600m / 天候 : 晴 / 芝 : 良 / 発走 : 15:35</span></p>
</dd></dl>
<p class="smalltxt">2026年5月24日 2回東京12日目 3歳以上オープン (国際)(特指)(定量)</p>
</div>
<table class="race_table_01 nk_tb_common" summary="レース結果">
<tr><th>着順</th><th>枠番</th><th>馬番</th><th>馬名</th><th>性齢</th><th>斤量</th><th>騎手</th>
<th>タイム</th><th>着差</th><th>通過</th><th>上り</th><th>単勝</th><th>人気</th><th>馬体重</th></tr>
<tr><td>1</td><td>3</td><td>5</td><td><a href="/horse/2021104567/">テストホース</a></td>
<td>牡4</td><td>57.0</td><td>某騎手</td><td>1:32.5</td><td></td><td>3-3</td><td>33.8</td>
<td>4.5</td><td>2</td><td>480(+2)</td></tr>
<tr><td>2</td><td>1</td><td>2</td><td><a href="/horse/2020105678/">セカンドホース</a></td>
<td>牝5</td><td>55.0</td><td>別騎手</td><td>1:32.7</td><td>1.1/4</td><td>5-4</td><td>33.5</td>
<td>12.3</td><td>6</td><td>452(-4)</td></tr>
<tr><td>取</td><td>7</td><td>14</td><td><a href="/horse/2021109999/">トリケシホース</a></td>
<td>牡4</td><td>57.0</td><td></td><td></td><td></td><td></td><td></td>
<td>---</td><td></td><td></td></tr>
</table>
</body></html>
"""

HORSE_HTML = """
<html><body>
<table class="blood_table" summary="血統">
<tr><td rowspan="2" class="b_ml"><a href="/horse/ped/000a010842/">ディープインパクト</a></td>
<td class="b_ml"><a>サンデーサイレンス</a></td></tr>
<tr><td class="b_fml"><a>ウインドインハーヘア</a></td></tr>
<tr><td rowspan="2" class="b_fml"><a>テストマザー</a></td>
<td class="b_ml"><a href="/horse/ped/000a008587/">キングカメハメハ</a></td></tr>
<tr><td class="b_fml"><a>テストグランマ</a></td></tr>
</table>
<table class="db_h_race_results nk_tb_common">
<tr><th>日付</th><th>開催</th><th>天気</th><th>R</th><th>レース名</th><th>頭数</th><th>枠番</th>
<th>馬番</th><th>オッズ</th><th>人気</th><th>着順</th><th>騎手</th><th>斤量</th><th>距離</th>
<th>馬場</th><th>タイム</th><th>着差</th></tr>
<tr><td>2026/04/12</td><td>2東京6</td><td>晴</td><td>11</td><td><a>テスト記念(GIII)</a></td>
<td>16</td><td>4</td><td>8</td><td>6.7</td><td>3</td><td>2</td><td>某</td><td>57</td>
<td>芝1800</td><td>良</td><td>1:46.2</td><td>0.1</td></tr>
<tr><td>2026/03/01</td><td>1阪神4</td><td>曇</td><td>10</td><td><a>3歳上2勝クラス</a></td>
<td>13</td><td>2</td><td>3</td><td>2.1</td><td>1</td><td>1</td><td>某</td><td>56</td>
<td>芝1600</td><td>稍重</td><td>1:34.0</td><td>-0.3</td></tr>
<tr><td>2026/02/01</td><td>1中山3</td><td>雪</td><td>5</td><td><a>障害3歳上未勝利</a></td>
<td>10</td><td>1</td><td>1</td><td>5.0</td><td>2</td><td>1</td><td>某</td><td>60</td>
<td>障2880</td><td>良</td><td>3:15.0</td><td>-0.5</td></tr>
<tr><td>2026/01/12</td><td>1中山5</td><td>晴</td><td>9</td><td><a>3歳上1勝クラス</a></td>
<td>15</td><td>6</td><td>11</td><td>8.8</td><td>4</td><td>取</td><td>某</td><td>56</td>
<td>芝1600</td><td>良</td><td></td><td></td></tr>
</table>
</body></html>
"""

LIST_HTML = """
<html><body>
<a href="/race/202605021211/">11R</a>
<a href="/race/202605021212/">12R</a>
<a href="/race/202609021201/">1R</a>
<a href="/race/202605021211/">重複リンク</a>
<a href="/horse/2021104567/">馬リンク</a>
</body></html>
"""


class TestParseTime:
    def test_minutes_format(self):
        assert parse_time("1:32.5") == 92.5

    def test_seconds_only(self):
        assert parse_time("58.9") == 58.9

    def test_invalid(self):
        assert parse_time("") is None
        assert parse_time("取消") is None


class TestDetectClass:
    def test_graded(self):
        assert detect_class("テスト記念(GIII)") == "G3"
        assert detect_class("テスト記念(GII)") == "G2"
        assert detect_class("テスト記念(GI)") == "G1"
        assert detect_class("フェブラリーS(G1)") == "G1"

    def test_conditions(self):
        assert detect_class("3歳上2勝クラス") == "2勝"
        assert detect_class("3歳未勝利") == "未勝利"
        assert detect_class("2歳新馬") == "新馬"

    def test_unknown_defaults(self):
        assert detect_class("なんとか特別") == "1勝"


class TestParseRacePage:
    def test_basic_fields(self):
        race = parse_race_page(RACE_HTML, "202605021211")
        assert race is not None
        assert race.name == "サンプルステークス"
        assert race.date == "2026-05-24"
        assert race.course == "東京"
        assert race.surface == "芝"
        assert race.distance == 1600
        assert race.going == "良"
        assert race.race_class == "OP"

    def test_result_rows(self):
        race = parse_race_page(RACE_HTML, "202605021211")
        assert len(race.rows) == 3
        first = race.rows[0]
        assert first.finish_position == 1
        assert first.name == "テストホース"
        assert first.horse_id == "2021104567"
        assert first.weight_carried == 57.0
        assert first.time_sec == 92.5
        assert first.odds == 4.5
        assert first.popularity == 2

    def test_scratched_horse(self):
        race = parse_race_page(RACE_HTML, "202605021211")
        scratched = race.rows[2]
        assert scratched.finish_position is None
        assert scratched.time_sec is None


class TestParseHorsePage:
    def test_pedigree(self):
        horse = parse_horse_page(HORSE_HTML, "2021104567")
        assert horse.sire == "ディープインパクト"
        assert horse.dam_sire == "キングカメハメハ"

    def test_past_races_skip_jump_and_scratched(self):
        horse = parse_horse_page(HORSE_HTML, "2021104567")
        # 障害戦と取消はスキップされ 2 走のみ
        assert len(horse.past_races) == 2
        latest = horse.past_races[0]
        assert latest["date"] == "2026-04-12"
        assert latest["course"] == "東京"
        assert latest["surface"] == "芝"
        assert latest["distance"] == 1800
        assert latest["time_sec"] == 106.2
        assert latest["race_class"] == "G3"
        assert horse.past_races[1]["going"] == "稍重"
        assert horse.past_races[1]["race_class"] == "2勝"


class TestParseRaceIds:
    def test_dedupe_and_sort(self):
        ids = parse_race_ids(LIST_HTML)
        assert ids == ["202605021211", "202605021212", "202609021201"]
