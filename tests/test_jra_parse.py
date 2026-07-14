"""JRA 成績ページパーサーのテスト。

フィクスチャは jra.go.jp の成績ページ構造を模し、実際の有馬記念2025の
確認済みデータ（勝ち馬ミュージアムマイル 2:31.5 良 など）を使用している。
JRA ページ特有の Shift_JIS・全角文字・カンマ入り距離表記を再現。
"""

import json

import pytest

pytest.importorskip("bs4")

from keiba.scrape.jra import parse_jra_result_page, to_dataset_race  # noqa: E402
from keiba.scrape.netkeiba import detect_encoding  # noqa: E402

ARIMA_HTML = """
<html><head>
<meta http-equiv="content-type" content="text/html; charset=Shift_JIS">
<title>2025年 有馬記念 JRA</title>
</head><body>
<h2>2025年 有馬記念</h2>
<p>第70回有馬記念（ＧⅠ）</p>
<p>2025年12月28日（日曜） 5回中山8日 11レース</p>
<p>サラブレッド系3歳以上オープン　芝・右 2,500メートル</p>
<p>天候：晴　馬場状態：良</p>
<table>
<tr><th>着順</th><th>枠番</th><th>馬番</th><th>馬名</th><th>性齢</th><th>負担重量</th>
<th>騎手名</th><th>タイム</th><th>着差</th><th>推定上り</th><th>馬体重(増減)</th>
<th>調教師名</th><th>単勝人気</th></tr>
<tr><td>1</td><td>3</td><td>5</td><td>ミュージアムマイル</td><td>牡3</td><td>56.0</td>
<td>Ｃ．デムーロ</td><td>2:31.5</td><td></td><td>34.8</td><td>486(+2)</td>
<td>（栗）高柳大輔</td><td>1</td></tr>
<tr><td>2</td><td>6</td><td>11</td><td>コスモキュランダ</td><td>牡4</td><td>58.0</td>
<td>某騎手</td><td>2:31.6</td><td>1/2馬身</td><td>34.9</td><td>488(0)</td>
<td>（美）加藤士津八</td><td>12</td></tr>
<tr><td>3</td><td>1</td><td>2</td><td>ダノンデサイル</td><td>牡4</td><td>58.0</td>
<td>某騎手</td><td>2:31.6</td><td>クビ</td><td>34.7</td><td>510(-4)</td>
<td>（美）安田翔伍</td><td>2</td></tr>
<tr><td>中止</td><td>8</td><td>16</td><td>チュウシホース</td><td>牡5</td><td>57.0</td>
<td>某騎手</td><td></td><td></td><td></td><td>480(+6)</td>
<td>（栗）某調教師</td><td>9</td></tr>
</table>
</body></html>
"""


class TestParseJraResultPage:
    def test_race_meta(self):
        race = parse_jra_result_page(ARIMA_HTML, "arima2025.html")
        assert race is not None
        assert race.date == "2025-12-28"
        assert race.course == "中山"
        assert race.surface == "芝"
        assert race.distance == 2500  # 「2,500メートル」のカンマを処理
        assert race.going == "良"
        assert race.race_class == "G1"  # 全角（ＧⅠ）→ NFKC → GI
        assert race.name == "有馬記念"

    def test_result_rows(self):
        race = parse_jra_result_page(ARIMA_HTML)
        assert len(race.rows) == 4
        winner = race.rows[0]
        assert winner.finish_position == 1
        assert winner.name == "ミュージアムマイル"
        assert winner.horse_number == 5
        assert winner.weight_carried == 56.0
        assert winner.time_sec == 151.5  # 2:31.5
        assert winner.popularity == 1
        assert race.rows[1].name == "コスモキュランダ"
        assert race.rows[1].time_sec == 151.6

    def test_cancelled_horse(self):
        race = parse_jra_result_page(ARIMA_HTML)
        cancelled = race.rows[3]
        assert cancelled.finish_position is None
        assert cancelled.time_sec is None

    def test_no_result_table_returns_none(self):
        assert parse_jra_result_page("<html><body><p>404</p></body></html>") is None


class TestToDatasetRace:
    def test_structure(self):
        parsed = parse_jra_result_page(ARIMA_HTML, "https://example/arima2025.html")
        entry = to_dataset_race(parsed)
        assert entry["race"]["course"] == "中山"
        assert entry["race"]["distance"] == 2500
        assert len(entry["horses"]) == 4
        winner = entry["horses"][0]
        assert winner["result"]["finish_position"] == 1
        assert winner["result"]["time_sec"] == 151.5

    def test_feeds_build_base_times(self, tmp_path):
        # scrape-jra の出力が build-base-times でそのまま使えること
        from keiba.cli import main

        parsed = parse_jra_result_page(ARIMA_HTML)
        dataset_path = tmp_path / "jra.json"
        out_path = tmp_path / "bt.json"
        with open(dataset_path, "w", encoding="utf-8") as f:
            json.dump({"races": [to_dataset_race(parsed)]}, f, ensure_ascii=False)

        code = main(["build-base-times", str(dataset_path), "-o", str(out_path), "--min-samples", "1"])
        assert code == 0
        with open(out_path, encoding="utf-8") as f:
            data = json.load(f)
        # G1補正(-2.0×2500/1600=-3.125)を戻した1勝級換算の平均
        assert "中山|芝|2500" in data["base_times"]


class TestDetectEncoding:
    def test_shift_jis(self):
        raw = '<meta charset="Shift_JIS">'.encode("ascii")
        assert detect_encoding(raw) == "cp932"

    def test_euc_jp_default(self):
        assert detect_encoding(b"<html><body>no charset</body></html>") == "euc-jp"

    def test_http_equiv_style(self):
        raw = b'<meta http-equiv="content-type" content="text/html; charset=EUC-JP">'
        assert detect_encoding(raw) == "euc-jp"


class TestScrapeJraCliWithLocalFile:
    def test_local_html_file(self, tmp_path, capsys):
        # URL の代わりに保存済み HTML ファイル（Shift_JIS）を処理できる
        from keiba.cli import main

        html_path = tmp_path / "arima2025.html"
        html_path.write_bytes(ARIMA_HTML.encode("cp932"))
        out_path = tmp_path / "results.json"

        code = main(["scrape-jra", str(html_path), "-o", str(out_path)])
        assert code == 0
        out = capsys.readouterr().out
        assert "中山" in out and "2500" in out

        with open(out_path, encoding="utf-8") as f:
            data = json.load(f)
        assert len(data["races"]) == 1
        assert data["races"][0]["horses"][0]["name"] == "ミュージアムマイル"
