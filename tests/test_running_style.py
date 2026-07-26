"""脚質×コース形態評価のテスト。"""

from keiba.models import HorseEntry, PastRace, RaceCard
from keiba.predictor import predict
from keiba.running_style import (
    course_style_aptitude,
    infer_style,
    style_fit_score,
)


def make_race(position_4c, field_size=16, **kwargs) -> PastRace:
    defaults = dict(
        date="2026-05-01", course="東京", surface="芝", distance=2000,
        going="良", time_sec=120.0, weight_carried=56.0,
        finish_position=5, field_size=field_size, race_class="OP",
        position_4c=position_4c,
    )
    defaults.update(kwargs)
    return PastRace(**defaults)


def make_horse(style=None, positions=()) -> HorseEntry:
    return HorseEntry(
        name="テスト", sire="キズナ", running_style=style,
        past_races=[make_race(p) for p in positions],
    )


class TestInferStyle:
    def test_explicit_style_wins(self):
        style, source = infer_style(make_horse(style="追込", positions=(1, 1, 1)))
        assert style == "追込"
        assert source == "明示指定"

    def test_front_runner_from_positions(self):
        # 過半数のレースで4角先頭 → 逃げ
        style, source = infer_style(make_horse(positions=(1, 1, 3)))
        assert style == "逃げ"
        assert source == "通過順位から推定"

    def test_stalker_from_positions(self):
        # 16頭中3-4番手 → 先行
        style, _ = infer_style(make_horse(positions=(3, 4, 4)))
        assert style == "先行"

    def test_closer_from_positions(self):
        # 16頭中8-10番手 → 差し
        style, _ = infer_style(make_horse(positions=(8, 10, 9)))
        assert style == "差し"

    def test_deep_closer_from_positions(self):
        # 16頭中14-16番手 → 追込
        style, _ = infer_style(make_horse(positions=(15, 16, 14)))
        assert style == "追込"

    def test_unknown_without_data(self):
        style, source = infer_style(make_horse())
        assert style is None
        assert source == "不明"


class TestCourseStyleAptitude:
    def test_small_track_favors_front(self):
        fukushima = course_style_aptitude("福島", "芝", 2000)
        assert fukushima["逃げ"] > fukushima["追込"]

    def test_long_stretch_favors_closers(self):
        tokyo = course_style_aptitude("東京", "芝", 1600)
        assert tokyo["差し"] > tokyo["逃げ"]

    def test_distance_override(self):
        # 新潟芝1000(直線競馬)は外枠先行有利の前残りコース
        straight = course_style_aptitude("新潟", "芝", 1000)
        outer = course_style_aptitude("新潟", "芝", 1600)
        assert straight["逃げ"] > outer["逃げ"]

    def test_unknown_course_falls_back_to_default(self):
        assert course_style_aptitude("門別", "ダ", 1200) == {
            "逃げ": 6, "先行": 7, "差し": 6, "追込": 4,
        }


class TestStyleFitScore:
    def test_front_runner_on_small_track(self):
        result = style_fit_score(make_horse(style="逃げ"), "福島", "芝", 2000)
        assert result["score"] == 80.0
        assert result["style"] == "逃げ"

    def test_closer_on_small_track_penalized(self):
        front = style_fit_score(make_horse(style="逃げ"), "福島", "芝", 2000)
        closer = style_fit_score(make_horse(style="追込"), "福島", "芝", 2000)
        assert front["score"] > closer["score"]

    def test_unknown_style_neutral(self):
        result = style_fit_score(make_horse(), "福島", "芝", 2000)
        assert result["score"] == 50.0
        assert result["style"] is None


class TestPredictWithStyle:
    def make_card(self, course):
        # 同一能力の逃げ馬と追込馬（脚質以外の条件を揃える）
        def horse(name, style):
            return {
                "name": name, "sire": "キズナ", "running_style": style,
                "weight_carried": 56.0,
                "past_races": [
                    {"date": "2026-05-01", "course": "東京", "surface": "芝",
                     "distance": 2000, "going": "良", "time_sec": 120.0,
                     "weight_carried": 56.0, "finish_position": 1,
                     "field_size": 10, "race_class": "OP"}
                ],
            }
        return RaceCard.from_dict(
            {
                "race": {"course": course, "surface": "芝", "distance": 2000, "going": "良"},
                "horses": [horse("ニゲウマ", "逃げ"), horse("オイコミウマ", "追込")],
            }
        )

    # 既定重みでは脚質は0(アブレーションで除外)のため、明示的に重みを与えて
    # 機構(推定・コース適合・偏差値化)が機能することを確認する
    STYLE_ON = {"style": 0.1}

    def test_front_runner_ranked_higher_on_small_track(self):
        results = predict(self.make_card("福島"), weights=self.STYLE_ON)
        assert results[0].name == "ニゲウマ"
        assert results[0].deviations["style"] > results[1].deviations["style"]
        assert results[0].style["style"] == "逃げ"

    def test_closer_gains_on_long_stretch(self):
        # 東京では差し追込有利（追込7 > 逃げ5）
        results = predict(self.make_card("東京"), weights=self.STYLE_ON)
        assert results[0].name == "オイコミウマ"
