from keiba.models import Workout
from keiba.workout import score_single_workout, workout_score


def make_workout(**kwargs) -> Workout:
    defaults = dict(
        date="2026-07-15",
        facility="栗東",
        course="坂路",
        furlongs=4,
        total_time=54.0,
        last_1f=13.2,
        intensity="強め",
        partner_result=None,
    )
    defaults.update(kwargs)
    return Workout(**defaults)


class TestSingleWorkout:
    def test_average_time_scores_around_60(self):
        # 平均水準（栗東坂路 54.0 / 終い 13.2）はほぼ 60 点
        score = score_single_workout(make_workout())
        assert 55 <= score <= 65

    def test_fast_time_scores_higher(self):
        fast = score_single_workout(make_workout(total_time=51.5, last_1f=12.2))
        slow = score_single_workout(make_workout(total_time=56.5, last_1f=14.2))
        assert fast > 85
        assert slow < 45

    def test_hanari_bonus(self):
        easy = score_single_workout(make_workout(intensity="馬なり"))
        hard = score_single_workout(make_workout(intensity="一杯"))
        # 同時計なら馬なり（余裕あり）の方が高評価
        assert easy > hard

    def test_partner_result(self):
        ahead = score_single_workout(make_workout(partner_result="先着"))
        behind = score_single_workout(make_workout(partner_result="遅れ"))
        assert ahead > behind

    def test_unknown_course_is_neutral(self):
        score = score_single_workout(make_workout(facility="その他", course="プール"))
        assert score == 50.0

    def test_furlong_scaling(self):
        # 5F しか計時していないコース追いは序盤1F(16.5秒)を加算して6F換算される
        w6 = make_workout(facility="美浦", course="W", furlongs=6, total_time=81.0, last_1f=12.0)
        w5 = make_workout(facility="美浦", course="W", furlongs=5, total_time=64.5, last_1f=12.0)
        # 64.5 + 16.5 = 81.0 なので同等の評価になる
        assert abs(score_single_workout(w6) - score_single_workout(w5)) < 1.0

    def test_furlong_scaling_not_linear(self):
        # 線形スケール(×6/5)だと5F計時が過大評価されることの回帰テスト:
        # 5F 66.8 は線形なら 80.2(excellent級)だが、換算後は 83.3(average級)
        w5 = make_workout(facility="美浦", course="W", furlongs=5, total_time=66.8, last_1f=12.9)
        w6 = make_workout(facility="美浦", course="W", furlongs=6, total_time=83.3, last_1f=12.9)
        assert abs(score_single_workout(w5) - score_single_workout(w6)) < 1.0


class TestWorkoutScore:
    def test_no_workouts_is_neutral(self):
        assert workout_score([])["score"] == 50.0

    def test_latest_weighted_higher(self):
        good_latest = workout_score([
            make_workout(total_time=51.5, last_1f=12.2),
            make_workout(date="2026-07-08", total_time=56.0, last_1f=14.0),
        ])
        bad_latest = workout_score([
            make_workout(total_time=56.0, last_1f=14.0),
            make_workout(date="2026-07-08", total_time=51.5, last_1f=12.2),
        ])
        assert good_latest["score"] > bad_latest["score"]
