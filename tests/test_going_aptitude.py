"""道悪適性評価のテスト。"""

from keiba.going_aptitude import (
    going_aptitude_score,
    is_wet,
    pedigree_wet_score,
    record_wet_score,
)
from keiba.models import HorseEntry, PastRace, RaceCard
from keiba.predictor import predict


def make_race(**kwargs) -> PastRace:
    defaults = dict(
        date="2026-05-01", course="東京", surface="芝", distance=2000,
        going="良", time_sec=120.0, weight_carried=56.0,
        finish_position=1, field_size=10, race_class="OP",
    )
    defaults.update(kwargs)
    return PastRace(**defaults)


class TestIsWet:
    def test_goings(self):
        assert not is_wet("良")
        assert is_wet("稍重")
        assert is_wet("重")
        assert is_wet("不良")


class TestPedigreeWetScore:
    def test_mudlark_sire_beats_firm_sire(self):
        # ゴールドシップ(道悪9) > ディープインパクト(道悪5)
        assert pedigree_wet_score("ゴールドシップ", None) > pedigree_wet_score(
            "ディープインパクト", None
        )

    def test_dam_sire_blended(self):
        base = pedigree_wet_score("ディープインパクト", None)
        with_wet_bms = pedigree_wet_score("ディープインパクト", "ゴールドシップ")
        assert with_wet_bms > base

    def test_unknown_sire_neutral(self):
        assert pedigree_wet_score("無名種牡馬", None) == 50.0


class TestRecordWetScore:
    def test_no_wet_starts_returns_none(self):
        assert record_wet_score([make_race(going="良")]) is None

    def test_wet_win_scores_100(self):
        races = [make_race(going="重", finish_position=1, field_size=10)]
        assert record_wet_score(races) == 100.0

    def test_wet_last_place_scores_0(self):
        races = [make_race(going="不良", finish_position=10, field_size=10)]
        assert record_wet_score(races) == 0.0

    def test_unknown_field_size_skipped(self):
        races = [make_race(going="重", finish_position=1, field_size=0)]
        assert record_wet_score(races) is None


class TestGoingAptitudeScore:
    def test_record_blended_when_available(self):
        # 道悪実績のある馬: 血統60% + 実績40%
        horse = HorseEntry(
            name="道悪巧者", sire="ゴールドシップ",
            past_races=[make_race(going="重", finish_position=1, field_size=10)],
        )
        result = going_aptitude_score(horse)
        assert result["record_wet"] == 100.0
        assert result["score"] == round(90.0 * 0.6 + 100.0 * 0.4, 1)
        assert result["wet_starts"] == 1

    def test_pedigree_only_without_record(self):
        horse = HorseEntry(name="良のみ", sire="ゴールドシップ", past_races=[make_race()])
        result = going_aptitude_score(horse)
        assert result["record_wet"] is None
        assert result["score"] == 90.0


def make_card(going: str) -> RaceCard:
    return RaceCard.from_dict(
        {
            "race": {"course": "福島", "surface": "芝", "distance": 2000, "going": going},
            "horses": [
                {
                    "name": "ムッドラーク", "sire": "ゴールドシップ",
                    "past_races": [
                        {"date": "2026-05-01", "course": "東京", "surface": "芝",
                         "distance": 2000, "going": "重", "time_sec": 121.0,
                         "weight_carried": 56.0, "finish_position": 1,
                         "field_size": 10, "race_class": "OP"}
                    ],
                },
                {
                    "name": "リョウバケン", "sire": "ゴールドシップ",
                    "past_races": [
                        {"date": "2026-05-01", "course": "東京", "surface": "芝",
                         "distance": 2000, "going": "重", "time_sec": 121.0,
                         "weight_carried": 56.0, "finish_position": 10,
                         "field_size": 10, "race_class": "OP"}
                    ],
                },
            ],
        }
    )


class TestPredictWithGoing:
    def test_dry_race_has_no_going_factor(self):
        results = predict(make_card("良"))
        assert all("going" not in r.deviations for r in results)
        assert all(r.going_aptitude is None for r in results)

    def test_wet_race_adds_going_factor(self):
        results = predict(make_card("重"))
        assert all("going" in r.deviations for r in results)
        assert all(r.going_aptitude is not None for r in results)
        mudlark = next(r for r in results if r.name == "ムッドラーク")
        firm = next(r for r in results if r.name == "リョウバケン")
        assert mudlark.deviations["going"] > firm.deviations["going"]

    def test_wetter_going_increases_factor_impact(self):
        # 同じメンバーなら、渋るほど道悪巧者と非巧者の総合点差が開く
        def gap(going):
            results = predict(make_card(going))
            scores = {r.name: r.total for r in results}
            return scores["ムッドラーク"] - scores["リョウバケン"]

        assert gap("不良") > gap("稍重") > 0
