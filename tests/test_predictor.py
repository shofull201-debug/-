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
        speed_only = predict(card, weights={"speed": 1.0, "workout": 0.0, "pedigree": 0.0})
        workout_only = predict(card, weights={"speed": 0.0, "workout": 1.0, "pedigree": 0.0})
        # 重み 1.0 の要素の偏差値がそのまま総合点になる
        for r in speed_only:
            assert abs(r.total - r.deviations["speed"]) < 0.11
        for r in workout_only:
            assert abs(r.total - r.deviations["workout"]) < 0.11

    def test_horse_without_past_races_gets_field_average_speed(self):
        # 過去走なしの馬を追加してもクラッシュせず、スピード偏差値は中位になる
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
        assert 40 <= rookie.deviations["speed"] <= 60
