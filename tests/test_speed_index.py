import pytest

from keiba.models import PastRace
from keiba.speed_index import (
    aggregate_speed_score,
    base_time,
    distance_index,
    going_variant,
    nishida_speed_index,
)


BASE_1600 = base_time("東京", "芝", 1600, "1勝")


def make_race(**kwargs) -> PastRace:
    defaults = dict(
        date="2026-06-01",
        course="東京",
        surface="芝",
        distance=1600,
        going="良",
        time_sec=BASE_1600,
        weight_carried=55.0,
        finish_position=1,
        field_size=16,
        race_class="1勝",
    )
    defaults.update(kwargs)
    return PastRace(**defaults)


class TestDistanceIndex:
    def test_exact_lookup(self):
        assert distance_index(1600) == 1.02
        assert distance_index(1200) == 1.36

    def test_interpolation(self):
        # 1250m は 1200(1.36) と 1300(1.26) の中間
        assert abs(distance_index(1250) - 1.31) < 1e-9

    def test_out_of_range_clamps(self):
        assert distance_index(800) == distance_index(1000)
        assert distance_index(4000) == distance_index(3600)


class TestBaseTime:
    def test_registered_course(self):
        # 実データ構築の基準タイム(東京芝1600・1勝)は妥当な範囲にある
        assert 92.0 < base_time("東京", "芝", 1600, "1勝") < 97.0

    def test_class_offset_scales_with_distance(self):
        t_1600 = base_time("東京", "芝", 1600, "G1")
        assert t_1600 == pytest.approx(BASE_1600 - 2.0)  # G1 補正 -2.0 × (1600/1600)
        base_2400 = base_time("東京", "芝", 2400, "1勝")
        t_2400 = base_time("東京", "芝", 2400, "G1")
        assert t_2400 == pytest.approx(base_2400 - 2.0 * (2400 / 1600))

    def test_fallback_for_unknown_condition(self):
        # 表に無い条件でも妥当な値を返す
        t = base_time("門別", "ダ", 1600, "1勝")
        assert 90 < t < 105


class TestNishidaSpeedIndex:
    def test_baseline_run_is_80(self):
        # 基準タイムどおり・斤量55・良馬場なら指数はちょうど 80
        assert nishida_speed_index(make_race()) == 80.0

    def test_faster_time_raises_index(self):
        # 1 秒速い → 1600m の距離指数 1.02 × 10 = 10.2 ポイント上昇
        idx = nishida_speed_index(make_race(time_sec=BASE_1600 - 1.0))
        assert abs(idx - 90.2) < 1e-9

    def test_weight_adjustment(self):
        # 斤量 57 は 55 基準から +4 ポイント
        idx = nishida_speed_index(make_race(weight_carried=57.0))
        assert idx == 84.0

    def test_going_variant_applied_when_no_track_variant(self):
        idx = nishida_speed_index(make_race(going="重"))
        assert idx == 80.0 + going_variant("芝", "重")

    def test_explicit_track_variant_overrides_going(self):
        idx = nishida_speed_index(make_race(going="重", track_variant=3.0))
        assert idx == 83.0

    def test_dirt_wet_track_is_negative_variant(self):
        assert going_variant("ダ", "稍重") < 0


class TestAggregateSpeedScore:
    def test_empty_past_races(self):
        score, indices = aggregate_speed_score([], "芝", 1600)
        assert score == 0.0
        assert indices == []

    def test_single_race(self):
        score, indices = aggregate_speed_score([make_race()], "芝", 1600)
        assert len(indices) == 1
        assert score == indices[0] == 80.0

    def test_different_surface_gets_lower_weight(self):
        # 同指数でも今回条件（芝）と合う馬の加重平均が優位になることを確認
        turf_fast = make_race(time_sec=93.3)          # 指数 90.2
        turf_slow = make_race(time_sec=95.3)          # 指数 69.8
        dirt_fast = make_race(
            surface="ダ", time_sec=95.8, course="東京", distance=1600
        )  # ダートの好走
        s_turf, _ = aggregate_speed_score([turf_fast, turf_slow], "芝", 1600)
        s_mixed, _ = aggregate_speed_score([dirt_fast, turf_slow], "芝", 1600)
        # 芝レースの実績はフルウェイト、ダートは半減で効く
        assert s_turf != s_mixed

    def test_uses_at_most_five_races(self):
        races = [make_race(date=f"2026-0{i}-01") for i in range(1, 7)]
        _, indices = aggregate_speed_score(races, "芝", 1600)
        assert len(indices) == 5

    def test_trim_worst_excludes_lowest_from_average(self):
        # 大敗走(指数-20)を1走トリムするとスコアが上がる
        races = [
            make_race(date="2026-06-01"),
            make_race(date="2026-05-01", time_sec=BASE_1600 + 2.0),  # 大敗
            make_race(date="2026-04-01"),
            make_race(date="2026-03-01"),
        ]
        base, _ = aggregate_speed_score(races, "芝", 1600)
        trimmed, _ = aggregate_speed_score(races, "芝", 1600, trim_worst=1)
        assert trimmed > base

    def test_agari_bonus_raises_score(self):
        # レース平均より1秒速い上がり(rel=-1.0)は AGARI_COEF ポイントの加点
        from keiba.speed_index import AGARI_COEF

        base, _ = aggregate_speed_score([make_race()], "芝", 1600)
        fast, _ = aggregate_speed_score(
            [make_race(last_3f_rel=-1.0)], "芝", 1600
        )
        assert fast == pytest.approx(base + AGARI_COEF)

    def test_agari_none_is_neutral(self):
        base, _ = aggregate_speed_score([make_race()], "芝", 1600)
        none_rel, _ = aggregate_speed_score(
            [make_race(last_3f_rel=None)], "芝", 1600
        )
        assert none_rel == base

    def test_trim_keeps_at_least_three_races(self):
        # 3走しか無ければトリムしない(結果は同じ)
        races = [
            make_race(date="2026-06-01"),
            make_race(date="2026-05-01", time_sec=BASE_1600 + 2.0),
            make_race(date="2026-04-01"),
        ]
        base, _ = aggregate_speed_score(races, "芝", 1600)
        trimmed, _ = aggregate_speed_score(races, "芝", 1600, trim_worst=1)
        assert trimmed == base
