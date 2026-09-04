"""スピード指数の算出式そのものの比較(西田式 vs 代替方式)。

集約(加重平均55+ベスト45)・上がり補正・他要素・重みは全て固定し、
1走あたりの指数式だけを入れ替える:

- nishida      : 現行 (基準タイム−タイム)×距離指数×10 + 馬場指数 + 斤量×2 + 80
- no_weight    : 斤量項を除いた西田式
- no_distidx   : 距離指数を使わず1秒=10pt固定
- beyer        : タイム差を基準タイム比の相対値で評価(米式の発想、1%≒10pt)
- zscore       : 条件(場×芝ダ×距離×クラス)ごとの実測平均・SDからのz値
                 (統計は学習期間のみから構築、検証期間へ持ち越し)

使い方:
    python scripts/tune_speed_formula.py data/dataset_2022_2026_v3.json.gz \
        --variants data/track_variants_2022_2026.json
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from statistics import mean, pstdev
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from keiba import predictor  # noqa: E402
from keiba.backtest import evaluate_weights, precompute  # noqa: E402
from keiba.scrape.dataset import load_dataset  # noqa: E402
from keiba.speed_index import (  # noqa: E402
    AGARI_COEF,
    RECENCY_WEIGHTS,
    _relevance,
    base_time,
    distance_index,
    going_variant,
    nishida_speed_index,
)
from keiba.track_variant import VariantTable  # noqa: E402

WEIGHTS = {"speed": 0.5, "workout": 0.0, "pedigree": 0.2, "going": 0.0,
           "style": 0.1, "connections": 0.1}
MIN_SAMPLES = 30  # z値方式の条件セルに必要な最小走数


def _variant_pts(r) -> float:
    if r.track_variant is not None:
        return r.track_variant
    return going_variant(r.surface, r.going)


def idx_nishida(r, stats=None):
    return nishida_speed_index(r)


def idx_no_weight(r, stats=None):
    base = base_time(r.course, r.surface, r.distance, r.race_class)
    return (base - r.time_sec) * distance_index(r.distance) * 10 + _variant_pts(r) + 80


def idx_no_distidx(r, stats=None):
    base = base_time(r.course, r.surface, r.distance, r.race_class)
    return ((base - r.time_sec) * 10 + _variant_pts(r)
            + (r.weight_carried - 55) * 2 + 80)


def idx_beyer(r, stats=None):
    base = base_time(r.course, r.surface, r.distance, r.race_class)
    return ((base - r.time_sec) / base * 1000 + _variant_pts(r)
            + (r.weight_carried - 55) * 2 + 80)


def _ability_time(time_sec, weight_carried, variant_pts, distance) -> float:
    """斤量と馬場を秒に換算して除いた「能力タイム」。"""
    variant_sec = variant_pts / (10 * distance_index(distance))
    return time_sec - (weight_carried - 55) * 0.2 - variant_sec


def build_zscore_stats(races: list[dict], variants) -> dict:
    cells = defaultdict(list)
    for race in races:
        info = race["race"]
        pts_table = variants.get(info["date"], info["course"], info["surface"]) \
            if variants else None
        for h in race["horses"]:
            t = h["result"]["time_sec"]
            pts = pts_table if pts_table is not None \
                else going_variant(info["surface"], info["going"])
            at = _ability_time(t, h["weight_carried"], pts, info["distance"])
            for key in (
                (info["course"], info["surface"], info["distance"], info["race_class"]),
                (info["surface"], info["distance"], info["race_class"]),
                (info["surface"], info["distance"]),
            ):
                cells[key].append(at)
    return {
        k: (mean(v), pstdev(v)) for k, v in cells.items()
        if len(v) >= MIN_SAMPLES and pstdev(v) > 0
    }


def idx_zscore(r, stats=None):
    at = _ability_time(r.time_sec, r.weight_carried, _variant_pts(r), r.distance)
    for key in (
        (r.course, r.surface, r.distance, r.race_class),
        (r.surface, r.distance, r.race_class),
        (r.surface, r.distance),
    ):
        if key in stats:
            mu, sigma = stats[key]
            return 80 + (mu - at) / sigma * 10
    return nishida_speed_index(r)  # 統計セルが無い条件はフォールバック


FORMULAS = [
    ("西田式(現行)", idx_nishida),
    ("斤量補正なし", idx_no_weight),
    ("距離指数なし(1秒=10pt)", idx_no_distidx),
    ("ベイヤー流(相対タイム差)", idx_beyer),
    ("z値方式(実測分布)", idx_zscore),
]


def make_aggregator(formula, stats):
    def agg(past_races, surface, distance, blend_best=None, trim_worst=None):
        if not past_races:
            return 0.0, []
        indices = [formula(r, stats) for r in past_races[:5]]
        effective = [
            idx - AGARI_COEF * r.last_3f_rel
            if AGARI_COEF and r.last_3f_rel is not None else idx
            for idx, r in zip(indices, past_races[:5])
        ]
        weights = [
            RECENCY_WEIGHTS[i] * _relevance(r, surface, distance)
            for i, r in enumerate(past_races[:5])
        ]
        wavg = sum(v * w for v, w in zip(effective, weights)) / sum(weights)
        return wavg * 0.55 + max(effective) * 0.45, indices
    return agg


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dataset")
    ap.add_argument("--variants")
    ap.add_argument("--split", default="2025-01-01")
    ap.add_argument("--min-date", default="2022-07-01")
    args = ap.parse_args()

    ds = load_dataset(args.dataset)
    races = [r for r in ds["races"] if r["race"]["date"] >= args.min_date]
    train = {"races": [r for r in races if r["race"]["date"] < args.split]}
    test = {"races": [r for r in races if r["race"]["date"] >= args.split]}
    variants = VariantTable.load(args.variants) if args.variants else None
    print(f"学習 {len(train['races'])} / 検証 {len(test['races'])} レース")

    stats = build_zscore_stats(train["races"], variants)
    print(f"z値方式の条件セル: {len(stats)}(学習期間のみから構築)")

    original = predictor.aggregate_speed_score
    try:
        for scope, subset in (("学習", train), ("検証", test)):
            print(f"\n=== {scope} ===")
            print(f"{'指数式':<24} {'◎勝率':>7} {'◎複勝率':>7} {'単回収':>7} {'複回収':>7}")
            for label, formula in FORMULAS:
                predictor.aggregate_speed_score = make_aggregator(formula, stats)
                m = evaluate_weights(precompute(subset, variants), WEIGHTS)
                print(f"{label:<24} {m['win_rate']*100:>6.1f}% "
                      f"{m['place_rate']*100:>6.1f}%"
                      f" {m['roi']:>6.1f}% {m['place_roi']:>6.1f}%")
    finally:
        predictor.aggregate_speed_score = original
    return 0


if __name__ == "__main__":
    sys.exit(main())
