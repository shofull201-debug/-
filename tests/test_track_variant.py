"""馬場指数の自動算出のテスト。"""

import json

from keiba.models import PastRace, RaceCard
from keiba.speed_index import base_time, nishida_speed_index
from keiba.track_variant import VariantTable, compute_variants

BASE_1600 = base_time("東京", "芝", 1600, "1勝")


def make_row(race_id, time_sec=None, date="2026-05-24", course="東京", surface="芝",
             distance=1600, race_class="1勝"):
    if time_sec is None:
        time_sec = BASE_1600
    return {
        "race_id": race_id,
        "date": date,
        "course": course,
        "surface": surface,
        "distance": distance,
        "race_class": race_class,
        "time_sec": time_sec,
        "going": "良",
    }


class TestComputeVariants:
    def test_slow_day_gives_positive_variant(self):
        # 東京芝1600の基準 94.3 に対し全馬 1 秒遅い日
        # → (95.3 - 94.3) × 10 × 1.02 = +10.2
        rows = [
            make_row("r1", BASE_1600 + 1), make_row("r1", BASE_1600 + 1),
            make_row("r2", BASE_1600 + 1), make_row("r2", BASE_1600 + 1),
        ]
        variants = compute_variants(rows)
        assert variants == {"2026-05-24|東京|芝": 10.2}

    def test_fast_day_gives_negative_variant(self):
        rows = [
            make_row("r1", BASE_1600 - 1), make_row("r1", BASE_1600 - 1),
            make_row("r2", BASE_1600 - 1),
        ]
        variants = compute_variants(rows)
        assert variants["2026-05-24|東京|芝"] == -10.2

    def test_median_across_races_robust_to_outlier_race(self):
        # 2レースは基準どおり、1レースだけ極端に速い（ハイレベル戦）
        rows = [
            make_row("r1"), make_row("r2"), make_row("r3", BASE_1600 - 3.3),
        ]
        variants = compute_variants(rows, min_races=3)
        # 中央値なので外れ値レースに引っ張られず 0.0
        assert variants["2026-05-24|東京|芝"] == 0.0

    def test_class_adjustment_used(self):
        # G1(補正-2.0)の勝ち時計は「1勝クラス基準-2秒」でちょうど基準どおり
        rows = [
            make_row("r1", BASE_1600 - 2.0, race_class="G1"),
            make_row("r2", BASE_1600 - 2.0, race_class="G1"),
        ]
        variants = compute_variants(rows)
        assert variants["2026-05-24|東京|芝"] == 0.0

    def test_min_races_filter(self):
        rows = [make_row("r1", BASE_1600 + 1)]
        assert compute_variants(rows, min_races=2) == {}
        assert "2026-05-24|東京|芝" in compute_variants(rows, min_races=1)

    def test_clamp(self):
        rows = [make_row("r1", BASE_1600 + 10), make_row("r2", BASE_1600 + 10)]  # +10秒の異常値
        variants = compute_variants(rows, clamp=40.0)
        assert variants["2026-05-24|東京|芝"] == 40.0

    def test_surfaces_separated(self):
        base_dirt = base_time("東京", "ダ", 1600, "1勝")
        rows = [
            make_row("r1", BASE_1600 + 1), make_row("r2", BASE_1600 + 1),
            make_row("r3", base_dirt, surface="ダ"), make_row("r4", base_dirt, surface="ダ"),
        ]
        variants = compute_variants(rows)
        assert variants["2026-05-24|東京|芝"] > 0
        assert variants["2026-05-24|東京|ダ"] == 0.0  # 東京ダ1600 基準 97.8 どおり


def make_past_race(**kwargs) -> PastRace:
    defaults = dict(
        date="2026-05-24", course="東京", surface="芝", distance=1600,
        going="重", time_sec=BASE_1600 + 1, weight_carried=55.0,
        finish_position=1, field_size=16, race_class="1勝",
    )
    defaults.update(kwargs)
    return PastRace(**defaults)


class TestVariantTable:
    def test_apply_fills_none_only(self):
        table = VariantTable({"2026-05-24|東京|芝": 10.2})
        races = [
            make_past_race(),                        # None → 適用される
            make_past_race(track_variant=3.0),       # 実測値あり → 触らない
            make_past_race(date="2026-05-17"),       # 表に無い日 → そのまま
        ]
        applied = table.apply_to_past_races(races)
        assert applied == 1
        assert races[0].track_variant == 10.2
        assert races[1].track_variant == 3.0
        assert races[2].track_variant is None

    def test_applied_variant_overrides_going_estimate(self):
        # 重馬場(概算+12)の日でも、実測の馬場指数(+10.2)が優先される
        race = make_past_race()
        VariantTable({"2026-05-24|東京|芝": 10.2}).apply_to_past_races([race])
        # 1秒遅い(-10.2) + 馬場指数(+10.2) = 基準どおりの 80
        assert nishida_speed_index(race) == 80.0

    def test_save_and_load_roundtrip(self, tmp_path):
        path = tmp_path / "variants.json"
        VariantTable({"2026-05-24|東京|芝": 10.2}).save(path)
        loaded = VariantTable.load(path)
        assert loaded.get("2026-05-24", "東京", "芝") == 10.2
        assert loaded.get("2026-05-24", "東京", "ダ") is None

    def test_apply_to_card(self):
        card = RaceCard.from_dict(
            {
                "race": {"course": "東京", "surface": "芝", "distance": 1600},
                "horses": [
                    {
                        "name": "テスト",
                        "sire": "ディープインパクト",
                        "past_races": [
                            {
                                "date": "2026-05-24", "course": "東京", "surface": "芝",
                                "distance": 1600, "going": "良", "time_sec": BASE_1600 + 1,
                                "weight_carried": 55.0, "finish_position": 1,
                                "field_size": 16, "race_class": "1勝",
                            }
                        ],
                    }
                ],
            }
        )
        applied = VariantTable({"2026-05-24|東京|芝": 10.2}).apply_to_card(card)
        assert applied == 1
        assert card.horses[0].past_races[0].track_variant == 10.2


class TestBuildVariantsCli:
    def test_end_to_end(self, tmp_path, capsys):
        from keiba.cli import main
        from test_backtest import make_dataset

        dataset_path = tmp_path / "dataset.json"
        out_path = tmp_path / "variants.json"
        with open(dataset_path, "w", encoding="utf-8") as f:
            json.dump(make_dataset(), f, ensure_ascii=False)

        code = main(["build-variants", str(dataset_path), "-o", str(out_path), "--min-races", "1"])
        assert code == 0
        table = VariantTable.load(out_path)
        assert len(table.table) > 0

        # optimize でも読めること
        code = main(
            ["optimize", str(dataset_path), "--step", "0.5", "--variants", str(out_path)]
        )
        assert code == 0
        assert "馬場指数表を適用" in capsys.readouterr().out
