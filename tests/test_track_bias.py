"""当日馬場バイアス測定のテスト。"""

from keiba.track_bias import race_front_bias


def make_race(pairs):
    """(4角通過, 着順) のリストからレースdictを作る。"""
    return {
        "horses": [
            {"result": {"position_4c": pos, "finish_position": fin}}
            for pos, fin in pairs
        ]
    }


class TestRaceFrontBias:
    def test_front_runners_winning_gives_positive_bias(self):
        # 4角先頭グループがそのまま上位入線 → 前残り(正)
        race = make_race([(i, i) for i in range(1, 13)])
        assert race_front_bias(race) > 0.5

    def test_closers_winning_gives_negative_bias(self):
        # 4角後方グループが差し切り → 差し有利(負)
        race = make_race([(i, 13 - i) for i in range(1, 13)])
        assert race_front_bias(race) < -0.5

    def test_small_field_returns_none(self):
        race = make_race([(1, 1), (2, 2), (3, 3)])
        assert race_front_bias(race) is None

    def test_missing_positions_returns_none(self):
        race = {
            "horses": [
                {"result": {"position_4c": None, "finish_position": i}}
                for i in range(1, 10)
            ]
        }
        assert race_front_bias(race) is None
