"""バックテスト・重み最適化のテスト（合成データセット使用）。"""

import json

from keiba.backtest import active_factors, evaluate_weights, grid_search, precompute


def make_dataset(n_races: int = 12, n_horses: int = 8) -> dict:
    """スピード指数が着順を完全に説明する合成データセット。

    血統はディープインパクト（芝向き）とヘニーヒューズ（ダート向き）を
    着順と無関係に交互に割り当て、ノイズにする。
    """
    races = []
    for r in range(n_races):
        horses = []
        for i in range(n_horses):
            # i が大きいほど速い past_races を持ち、実際の着順も上位
            time = 94.3 - i * 0.3
            horses.append(
                {
                    "name": f"馬{r}-{i}",
                    "horse_number": i + 1,
                    "sire": "ディープインパクト" if (r + i) % 2 == 0 else "ヘニーヒューズ",
                    "weight_carried": 56.0,
                    "past_races": [
                        {
                            "date": f"2026-0{1 + k}-01",
                            "course": "東京",
                            "surface": "芝",
                            "distance": 1600,
                            "going": "良",
                            "time_sec": time + k * 0.1,
                            "weight_carried": 56.0,
                            "finish_position": n_horses - i,
                            "field_size": n_horses,
                            "race_class": "1勝",
                        }
                        for k in range(3)
                    ],
                    "workouts": [],
                    "result": {
                        "finish_position": n_horses - i,
                        "time_sec": time,
                        "odds": 5.0,
                        "popularity": n_horses - i,
                    },
                }
            )
        races.append(
            {
                "race": {
                    "race_id": f"20260502{r:04d}",
                    "name": f"テスト{r}",
                    "date": "2026-05-01",
                    "course": "東京",
                    "surface": "芝",
                    "distance": 1600,
                    "going": "良",
                    "race_class": "2勝",
                },
                "horses": horses,
            }
        )
    return {"races": races}


class TestPrecompute:
    def test_shapes(self):
        dataset = make_dataset(3, 6)
        precomp = precompute(dataset)
        assert len(precomp) == 3
        assert len(precomp[0].deviations) == 6
        assert set(precomp[0].deviations[0]) == {
            "speed", "pedigree", "workout", "going", "style",
        }

    def test_workout_inactive_without_data(self):
        precomp = precompute(make_dataset())
        factors = active_factors(precomp)
        assert "workout" not in factors  # 追切データなし → 全馬50点 → 最適化対象外
        assert "speed" in factors
        assert "pedigree" in factors


class TestEvaluateWeights:
    def test_speed_only_perfectly_predicts(self):
        precomp = precompute(make_dataset())
        m = evaluate_weights(precomp, {"speed": 1.0, "pedigree": 0.0, "workout": 0.0})
        assert m["win_rate"] == 1.0
        assert m["place_rate"] == 1.0
        # 全レース単勝5.0倍的中 → 回収率500%
        assert m["roi"] == 500.0
        assert m["top3_hit"] == 3.0

    def test_pedigree_only_is_noise(self):
        precomp = precompute(make_dataset())
        m = evaluate_weights(precomp, {"speed": 0.0, "pedigree": 1.0, "workout": 0.0})
        assert m["win_rate"] < 1.0


class TestGridSearch:
    def test_finds_speed_dominant_weights(self):
        precomp = precompute(make_dataset())
        results = grid_search(precomp, step=0.25, objective="win_rate")
        best = results[0]
        assert best["win_rate"] == 1.0
        assert best["weights"]["speed"] >= 0.5
        assert best["weights"]["workout"] == 0.0  # 非アクティブ要素は0固定

    def test_results_sorted_by_objective(self):
        precomp = precompute(make_dataset())
        results = grid_search(precomp, step=0.25, objective="place_rate")
        rates = [r["place_rate"] for r in results]
        assert rates == sorted(rates, reverse=True)


class TestOptimizeCli:
    def test_end_to_end(self, tmp_path, capsys):
        from keiba.cli import main

        dataset_path = tmp_path / "dataset.json"
        weights_path = tmp_path / "weights.json"
        with open(dataset_path, "w", encoding="utf-8") as f:
            json.dump(make_dataset(), f, ensure_ascii=False)

        code = main(
            ["optimize", str(dataset_path), "--step", "0.25", "-o", str(weights_path)]
        )
        assert code == 0
        out = capsys.readouterr().out
        assert "最適重み" in out

        with open(weights_path, encoding="utf-8") as f:
            weights = json.load(f)
        assert weights["speed"] >= 0.5

    def test_build_base_times_from_dataset(self, tmp_path, capsys):
        from keiba.cli import main

        dataset_path = tmp_path / "dataset.json"
        out_path = tmp_path / "bt.json"
        with open(dataset_path, "w", encoding="utf-8") as f:
            json.dump(make_dataset(), f, ensure_ascii=False)

        code = main(
            ["build-base-times", str(dataset_path), "-o", str(out_path), "--min-samples", "5"]
        )
        assert code == 0
        with open(out_path, encoding="utf-8") as f:
            data = json.load(f)
        assert "東京|芝|1600" in data["base_times"]
        # 2勝クラス補正(-0.5)を戻した 1勝相当の平均になっている
        times = [94.3 - i * 0.3 for i in range(8)]
        expected = round(sum(times) / len(times) + 0.5, 1)
        assert data["base_times"]["東京|芝|1600"] == expected
