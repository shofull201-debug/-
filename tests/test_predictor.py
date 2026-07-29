import json
from pathlib import Path

from keiba.models import RaceCard
from keiba.predictor import predict

SAMPLE = Path(__file__).parent.parent / "data" / "sample_race.json"


def load_sample() -> RaceCard:
    with open(SAMPLE, encoding="utf-8") as f:
        return RaceCard.from_dict(json.load(f))


class TestPredict:
    def test_returns_all_horses_ranked(self):
        card = load_sample()
        results = predict(card)
        assert len(results) == len(card.horses)
        assert [r.rank for r in results] == list(range(1, len(results) + 1))
        # 総合点の降順であること
        totals = [r.total for r in results]
        assert totals == sorted(totals, reverse=True)

    def test_marks_assigned_to_top_five(self):
        results = predict(load_sample())
        assert results[0].mark == "◎"
        assert results[1].mark == "○"
        assert results[2].mark == "▲"
        assert results[3].mark == "△"

    def test_deviations_average_50(self):
        results = predict(load_sample())
        for key in ("speed", "pedigree", "workout"):
            values = [r.deviations[key] for r in results]
            assert abs(sum(values) / len(values) - 50.0) < 0.5

    def test_dirt_specialist_not_top_on_turf(self):
        # ダート馬（ヘニーヒューズ産駒・全走ダート）が芝マイル戦で◎にならない
        results = predict(load_sample())
        dirt_horse = next(r for r in results if r.name == "ダートモンスター")
        assert dirt_horse.rank > 1

    def test_custom_weights_change_ranking_inputs(self):
        card = load_sample()
        speed_only = predict(
            card, weights={"speed": 1.0, "workout": 0.0, "pedigree": 0.0, "style": 0.0}
        )
        workout_only = predict(
            card, weights={"speed": 0.0, "workout": 1.0, "pedigree": 0.0, "style": 0.0}
        )
        # 重み 1.0 の要素の偏差値がそのまま総合点になる
        for r in speed_only:
            assert abs(r.total - r.deviations["speed"]) < 0.11
        for r in workout_only:
            assert abs(r.total - r.deviations["workout"]) < 0.11

    def test_horse_without_past_races_treated_as_missing(self):
        # 過去走なしの馬はスピード偏差値が欠損(None)になり、クラッシュしない
        card = load_sample()
        data = {
            "name": "新馬クン",
            "sire": "キズナ",
            "weight_carried": 54.0,
            "past_races": [],
            "workouts": [],
        }
        from keiba.models import HorseEntry

        card.horses.append(HorseEntry.from_dict(data))
        results = predict(card)
        rookie = next(r for r in results if r.name == "新馬クン")
        assert rookie.speed_indices == []
        assert rookie.deviations["speed"] is None
        assert rookie.deviations["workout"] is None
        # 残る血統(キズナ=登録済み)へ重みが全振りされ、総合=血統偏差値
        assert abs(rookie.total - rookie.deviations["pedigree"]) < 0.11


class TestMissingFactorRedistribution:
    def make_card(self, extra_horse: dict):
        card = load_sample()
        from keiba.models import HorseEntry

        card.horses.append(HorseEntry.from_dict(extra_horse))
        return card

    def base_horse(self, **over) -> dict:
        data = {
            "name": "テスト馬",
            "sire": "ディープインパクト",
            "dam_sire": "キングカメハメハ",
            "weight_carried": 56.0,
            "past_races": [
                {
                    "date": "2026-06-07", "course": "東京", "surface": "芝",
                    "distance": 1600, "going": "良", "time_sec": 93.0,
                    "weight_carried": 56.0, "finish_position": 1,
                    "field_size": 14, "race_class": "OP",
                }
            ],
            "workouts": [],
        }
        data.update(over)
        return data

    def test_no_workout_is_missing_not_penalized(self):
        # 追切なし → 調偏 None、総合は残り要素の再配分で計算される
        from keiba.predictor import DEFAULT_WEIGHTS

        results = predict(self.make_card(self.base_horse()))
        r = next(x for x in results if x.name == "テスト馬")
        assert r.deviations["workout"] is None
        ws, wp = DEFAULT_WEIGHTS["speed"], DEFAULT_WEIGHTS["pedigree"]
        expected = (
            r.deviations["speed"] * ws + r.deviations["pedigree"] * wp
        ) / (ws + wp)
        assert abs(r.total - expected) < 0.15

    def test_missing_horse_excluded_from_deviation_pool(self):
        # 追切なし馬の中立値50が母集団に混ざらない:
        # データがある馬だけで平均をとると偏差値の平均はちょうど50になる
        results = predict(self.make_card(self.base_horse()))
        devs = [
            r.deviations["workout"] for r in results
            if r.deviations["workout"] is not None
        ]
        assert abs(sum(devs) / len(devs) - 50.0) < 1e-6
        # 順序はデータあり馬どうしの生スコア比較そのもの（縮小は単調変換）
        card = load_sample()
        full = predict(card)
        order_full = [r.name for r in sorted(full, key=lambda x: x.deviations["workout"])]
        order_sub = [
            r.name for r in sorted(
                (x for x in results if x.deviations["workout"] is not None),
                key=lambda x: x.deviations["workout"],
            )
        ]
        assert order_full == order_sub

    def test_small_pool_deviation_shrinks_to_neutral(self):
        # 母集団が小さいほど偏差値が50へ縮む(√(n/N)倍)
        card = self.make_card(self.base_horse())
        n, big_n = len(card.horses) - 1, len(card.horses)
        results = predict(card)
        full = {r.name: r.deviations["workout"] for r in predict(load_sample())}
        shrink = (n / big_n) ** 0.5
        for r in results:
            dev = r.deviations["workout"]
            if dev is None or r.name == "テスト馬":
                continue
            # 同じ6頭の母集団なので、縮小率だけが違う
            expected = 50.0 + (full[r.name] - 50.0) / 1.0 * shrink
            assert abs(dev - expected) < 0.11

    def test_unknown_pedigree_is_missing(self):
        # 適性表に無い種牡馬・母父なし → 血偏 None
        horse = self.base_horse(sire="無名種牡馬XYZ", dam_sire=None)
        results = predict(self.make_card(horse))
        r = next(x for x in results if x.name == "テスト馬")
        assert r.deviations["pedigree"] is None

    def test_full_data_horse_unaffected_shape(self):
        # 全要素そろっている馬は従来どおり全偏差値が数値
        results = predict(load_sample())
        for r in results:
            assert r.deviations["speed"] is not None
            assert r.deviations["workout"] is not None
            assert r.deviations["pedigree"] is not None


class TestTodayImpost:
    def test_heavier_today_weight_lowers_speed_score(self):
        from keiba.models import HorseEntry
        from keiba.predictor import evaluate_horse

        base = {
            "name": "斤量テスト", "sire": "キズナ", "weight_carried": 55.0,
            "past_races": [
                {"date": "2026-06-07", "course": "東京", "surface": "芝",
                 "distance": 1600, "going": "良", "time_sec": 93.0,
                 "weight_carried": 56.0, "finish_position": 1,
                 "field_size": 14, "race_class": "OP"}
            ],
        }
        light = evaluate_horse(HorseEntry.from_dict(base), "芝", 1600)
        heavy = evaluate_horse(
            HorseEntry.from_dict({**base, "weight_carried": 57.0}), "芝", 1600
        )
        # 今回+2kg → 西田式換算で 4pt 減
        assert light["speed"] - heavy["speed"] == 4.0
