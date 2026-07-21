"""追い切り記事テキスト取り込みのテスト。"""

import json

from keiba.workout_import import extract_workouts, merge_into_card, parse_workout_block

ARTICLE = """
【テスト記念2026追い切り評価/全頭診断】

アルファホース 評価S
6月25日、栗東坂路で52.3-38.1-24.6-12.1を馬なりでマーク。
抜群の動きで文句なしの仕上がり。

ベータスター 評価B
6月25日に美浦Wコースで66.8-51.9-37.5-11.8。強めに追われ、
併せた古馬オープン馬に先着した。

ガンマウインド 評価C
コメントのみで時計の記載なし。
"""


class TestParseBlock:
    def test_hanro(self):
        w = parse_workout_block(
            "6月25日、栗東坂路で52.3-38.1-24.6-12.1を馬なりでマーク。", 2026
        )
        assert w["facility"] == "栗東"
        assert w["course"] == "坂路"
        assert w["total_time"] == 52.3
        assert w["last_1f"] == 12.1
        assert w["furlongs"] == 4
        assert w["intensity"] == "馬なり"
        assert w["date"] == "2026-06-25"
        assert w["partner_result"] is None

    def test_course_with_partner(self):
        w = parse_workout_block(
            "美浦Wコースで66.8-51.9-37.5-11.8。強めに追われ、併せ馬に先着。", 2026
        )
        assert w["facility"] == "美浦"
        assert w["course"] == "W"
        assert w["intensity"] == "強め"
        assert w["partner_result"] == "先着"

    def test_no_times_returns_none(self):
        assert parse_workout_block("時計の記載なし。素軽い動き。", 2026) is None

    def test_increasing_sequence_rejected(self):
        # ラップ表記(増加列)は通し時計ではないので捨てる
        assert parse_workout_block("12.1-24.6-38.1-52.3", 2026) is None


class TestExtract:
    def test_extracts_per_horse(self):
        names = ["アルファホース", "ベータスター", "ガンマウインド", "デルタ不在"]
        found = extract_workouts(ARTICLE, names, 2026)
        assert set(found) == {"アルファホース", "ベータスター"}
        assert found["アルファホース"]["course"] == "坂路"
        assert found["ベータスター"]["partner_result"] == "先着"

    def test_html_input(self):
        html = "<html><body><p>アルファホース</p><p>栗東坂路 52.3-38.1-24.6-12.1 馬なり</p></body></html>"
        found = extract_workouts(html, ["アルファホース"], 2026)
        assert found["アルファホース"]["total_time"] == 52.3


class TestMergeAndCli:
    def make_card(self):
        return {
            "race": {"name": "テスト記念", "date": "2026-06-28", "course": "阪神",
                     "surface": "芝", "distance": 2200, "going": "良",
                     "race_class": "G1"},
            "horses": [
                {"name": "アルファホース", "sire": "キズナ", "past_races": [],
                 "workouts": []},
                {"name": "ベータスター", "sire": "モーリス", "past_races": [],
                 "workouts": []},
            ],
        }

    def test_merge_keeps_existing_by_default(self):
        card = self.make_card()
        card["horses"][0]["workouts"] = [{
            "date": "2026-06-20", "facility": "栗東", "course": "坂路",
            "furlongs": 4, "total_time": 53.0, "last_1f": 12.5,
        }]
        found = extract_workouts(ARTICLE, ["アルファホース", "ベータスター"], 2026)
        applied = merge_into_card(card, found)
        assert applied == 1  # 既存ありのアルファはスキップ、ベータのみ
        assert card["horses"][0]["workouts"][0]["total_time"] == 53.0

    def test_cli_end_to_end(self, tmp_path, capsys):
        from keiba.cli import main

        card_path = tmp_path / "card.json"
        card_path.write_text(json.dumps(self.make_card(), ensure_ascii=False),
                             encoding="utf-8")
        article_path = tmp_path / "article.txt"
        article_path.write_text(ARTICLE, encoding="utf-8")

        code = main(["import-workouts", str(card_path), str(article_path)])
        assert code == 0
        card = json.loads(card_path.read_text(encoding="utf-8"))
        assert card["horses"][0]["workouts"][0]["course"] == "坂路"
        assert card["horses"][1]["workouts"][0]["partner_result"] == "先着"
        out = capsys.readouterr().out
        assert "2 頭に追切を設定" in out