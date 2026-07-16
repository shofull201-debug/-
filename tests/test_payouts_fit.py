"""払戻データ収集・複勝回収率・学習ベース重み最適化(fit)のテスト。"""

import json

import pytest

from keiba.backtest import evaluate_weights, precompute
from test_backtest import make_dataset

PAY_HTML = """
<html><body>
<table class="pay_table_01">
<tr><th class="tan">単勝</th><td>5</td><td>450</td><td>2</td></tr>
<tr><th class="fuku">複勝</th><td>5<br>2<br>11</td><td>160<br>210<br>1,310</td><td>1<br>4<br>6</td></tr>
<tr><th class="uren">馬連</th><td>2 - 5</td><td>1,230</td><td>3</td></tr>
</table>
</body></html>
"""


class TestParsePayouts:
    def test_win_and_place(self):
        pytest.importorskip("bs4")
        from keiba.scrape.netkeiba import parse_payouts

        payouts = parse_payouts(PAY_HTML)
        assert payouts["win"] == {5: 450}
        assert payouts["place"] == {5: 160, 2: 210, 11: 1310}  # カンマ入り金額も処理

    def test_no_table(self):
        pytest.importorskip("bs4")
        from keiba.scrape.netkeiba import parse_payouts

        assert parse_payouts("<html></html>") == {"win": {}, "place": {}}


def dataset_with_payouts() -> dict:
    """合成データセットに払戻を付与する(1着=単勝300円、1-3着=複勝110/150/200円)。"""
    dataset = make_dataset(10, 8)
    for race_data in dataset["races"]:
        win, place = {}, {}
        for h in race_data["horses"]:
            pos = h["result"]["finish_position"]
            num = h["horse_number"]
            if pos == 1:
                win[num] = 300
                place[num] = 110
            elif pos == 2:
                place[num] = 150
            elif pos == 3:
                place[num] = 200
        race_data["payouts"] = {"win": win, "place": place}
    return dataset


class TestPayoutRoi:
    def test_win_payout_overrides_odds(self):
        precomp = precompute(dataset_with_payouts())
        m = evaluate_weights(precomp, {"speed": 1.0})
        # 全レース◎的中: 単勝は払戻300円ベース(オッズ5.0=500円ではなく)
        assert m["win_rate"] == 1.0
        assert m["roi"] == 300.0
        assert m["place_roi"] == 110.0  # ◎は常に1着なので複勝110円

    def test_json_roundtrip_string_keys(self, tmp_path):
        # JSON保存で払戻のキーが文字列になっても正しく読める
        path = tmp_path / "ds.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(dataset_with_payouts(), f, ensure_ascii=False)
        with open(path, encoding="utf-8") as f:
            precomp = precompute(json.load(f))
        m = evaluate_weights(precomp, {"speed": 1.0})
        assert m["place_roi"] == 110.0

    def test_no_payouts_gives_zero_place_roi(self):
        precomp = precompute(make_dataset(5, 8))
        m = evaluate_weights(precomp, {"speed": 1.0})
        assert m["place_roi"] == 0.0
        assert m["roi"] == 500.0  # オッズ5.0にフォールバック


class TestFitCli:
    def test_learns_speed_dominant_weights(self, tmp_path, capsys):
        pytest.importorskip("sklearn")
        from keiba.cli import main

        dataset_path = tmp_path / "dataset.json"
        weights_path = tmp_path / "weights.json"
        with open(dataset_path, "w", encoding="utf-8") as f:
            json.dump(dataset_with_payouts(), f, ensure_ascii=False)

        code = main(["fit", str(dataset_path), "-o", str(weights_path)])
        assert code == 0
        out = capsys.readouterr().out
        assert "学習した重み" in out

        with open(weights_path, encoding="utf-8") as f:
            weights = json.load(f)
        # 着順を完全に説明するspeedが支配的な重みになる
        assert weights["speed"] >= 0.6
        assert abs(sum(weights.values()) - 1.0) < 0.01
