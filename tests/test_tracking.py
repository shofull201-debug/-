"""前向き予想ログのテスト。"""

import json

from keiba.tracking import load_log, set_result, summarize


def make_log(tmp_path):
    entries = [
        {
            "race": {"name": "テスト記念", "date": "2026-07-26", "course": "新潟",
                     "surface": "芝", "distance": 1600, "going": "良",
                     "race_class": "G3"},
            "picks": [
                {"rank": 1, "mark": "◎", "horse_number": 3, "name": "アルファ", "total": 60.0},
                {"rank": 2, "mark": "○", "horse_number": 4, "name": "ベータ", "total": 58.0},
                {"rank": 3, "mark": "▲", "horse_number": 8, "name": "ガンマ", "total": 56.0},
                {"rank": 4, "mark": "△", "horse_number": 1, "name": "デルタ", "total": 54.0},
                {"rank": 5, "mark": "△", "horse_number": 2, "name": "イプシロン", "total": 52.0},
                {"rank": 6, "mark": "", "horse_number": 5, "name": "ゼータ", "total": 50.0},
            ],
            "result": None,
        }
    ]
    path = tmp_path / "log.json"
    path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    return str(path)


class TestSetResultAndSummarize:
    def test_win_and_metrics(self, tmp_path):
        path = make_log(tmp_path)
        set_result(path, "テスト記念", ["アルファ", "ゼータ", "ベータ"],
                   win_pay=420, place_pays={"アルファ": 180})
        s = summarize(load_log(path))
        assert s["n_finished"] == 1
        assert s["win_rate"] == 1.0
        assert s["place_rate"] == 1.0
        assert s["cover3_avg"] == 2  # 印5頭中: アルファ・ベータ
        assert s["box3_rate"] == 0.0  # ゼータ(印外)が2着
        assert s["tan_roi"] == 420.0
        assert s["fuku_roi"] == 180.0

    def test_miss_counts_as_zero_return(self, tmp_path):
        path = make_log(tmp_path)
        set_result(path, "テスト記念", ["ゼータ", "デルタ", "ガンマ"])
        s = summarize(load_log(path))
        assert s["win_rate"] == 0.0
        assert s["place_rate"] == 0.0
        assert s["tan_roi"] == 0.0  # ◎不的中は払戻未入力でも0円として計上
        assert s["cover3_avg"] == 2  # デルタ・ガンマ

    def test_win_without_payout_excluded_from_roi(self, tmp_path):
        path = make_log(tmp_path)
        set_result(path, "テスト記念", ["アルファ", "ベータ", "ガンマ"])
        s = summarize(load_log(path))
        assert s["win_rate"] == 1.0
        assert s["tan_n"] == 0        # 払戻不明の的中はROIから除外
        assert s["tan_roi"] is None

    def test_unknown_horse_rejected(self, tmp_path):
        path = make_log(tmp_path)
        try:
            set_result(path, "テスト記念", ["存在しない馬", "ベータ", "ガンマ"])
            raise AssertionError("must raise")
        except ValueError:
            pass

    def test_cli_end_to_end(self, tmp_path, capsys):
        from keiba.cli import main

        path = make_log(tmp_path)
        code = main(["record-result", "テスト記念", "アルファ,ベータ,ガンマ",
                     "--log", path, "--win-pay", "420",
                     "--place-pays", "アルファ=180"])
        assert code == 0
        code = main(["report", "--log", path])
        assert code == 0
        out = capsys.readouterr().out
        assert "◎勝率 100.0%" in out
        assert "単勝回収 420.0%" in out