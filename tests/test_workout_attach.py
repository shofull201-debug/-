"""調教好タイム索引の適用テスト。"""

from keiba.workout_attach import attach_to_card, workouts_for

INDEX = {
    "テスト馬": [
        ["2026-06-20", "栗東", 53.5, 12.8],
        ["2026-07-05", "栗東", 52.1, 12.2],
        ["2026-07-15", "栗東", 53.0, 12.5],
    ],
    "古い馬": [["2026-01-10", "美浦", 54.0, 13.0]],
}


class TestWorkoutsFor:
    def test_latest_plus_best(self):
        works = workouts_for(INDEX, "テスト馬", "2026-07-19")
        assert len(works) == 2
        assert works[0]["date"] == "2026-07-15"     # 直近
        assert works[1]["total_time"] == 52.1        # 窓内ベスト
        assert works[0]["course"] == "坂路"

    def test_window_excludes_old_and_same_day(self):
        assert workouts_for(INDEX, "古い馬", "2026-07-19") == []
        # レース当日の調教は使わない
        assert workouts_for(
            {"a": [["2026-07-19", "栗東", 52.0, 12.0]]}, "a", "2026-07-19"
        ) == []

    def test_unknown_horse(self):
        assert workouts_for(INDEX, "未収録", "2026-07-19") == []


class TestAttachToCard:
    def make_card(self):
        return {
            "race": {"date": "2026-07-19"},
            "horses": [
                {"name": "テスト馬", "workouts": []},
                {"name": "未収録", "workouts": []},
                {"name": "古い馬", "workouts": [
                    {"date": "2026-07-10", "facility": "美浦", "course": "W",
                     "furlongs": 6, "total_time": 80.0, "last_1f": 11.9}
                ]},
            ],
        }

    def test_attach(self):
        card = self.make_card()
        applied = attach_to_card(card, INDEX)
        assert applied == 1
        assert len(card["horses"][0]["workouts"]) == 2
        # 既存の追切(記事由来など)は保持
        assert card["horses"][2]["workouts"][0]["course"] == "W"
        assert card["horses"][1]["workouts"] == []
