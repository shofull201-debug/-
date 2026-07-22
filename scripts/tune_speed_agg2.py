"""過去5走スピード指数の集約方法の比較: 単純平均 / 加重平均 / 中央値 / 現行。

すべて上がり3F補正後の指数に対して適用し、他要素・重みは固定。
学習(2022-07〜2024-12)と検証(2025-01〜)で一貫するかを見る。

使い方:
    python scripts/tune_speed_agg2.py data/dataset_2022_2026_full.json.gz \
        --variants data/track_variants_2022_2026.json
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from keiba import predictor, speed_index  # noqa: E402
from keiba.backtest import evaluate_weights, precompute  # noqa: E402
from keiba.scrape.dataset import load_dataset  # noqa: E402
from keiba.speed_index import (  # noqa: E402
    AGARI_COEF,
    RECENCY_WEIGHTS,
    _relevance,
    nishida_speed_index,
)
from keiba.track_variant import VariantTable  # noqa: E402

WEIGHTS = {"speed": 0.5, "workout": 0.0, "pedigree": 0.2, "going": 0.0,
           "style": 0.1, "connections": 0.1}


def make_aggregator(mode: str):
    """aggregate_speed_score 互換の関数を返す。"""

    def agg(past_races, surface, distance, blend_best=None, trim_worst=None):
        if not past_races:
            return 0.0, []
        indices = [nishida_speed_index(r) for r in past_races[:5]]
        effective = [
            idx - AGARI_COEF * r.last_3f_rel
            if AGARI_COEF and r.last_3f_rel is not None else idx
            for idx, r in zip(indices, past_races[:5])
        ]
        if mode == "mean":
            return sum(effective) / len(effective), indices
        if mode == "median":
            return statistics.median(effective), indices
        weights = [
            RECENCY_WEIGHTS[i] * _relevance(r, surface, distance)
            for i, r in enumerate(past_races[:5])
        ]
        wavg = sum(v * w for v, w in zip(effective, weights)) / sum(weights)
        if mode == "wavg":
            return wavg, indices
        if mode == "median_best":
            med = statistics.median(effective)
            return med * 0.55 + max(effective) * 0.45, indices
        return wavg * 0.55 + max(effective) * 0.45, indices  # current

    return agg


MODES = [
    ("現行(加重平均55+ベスト45)", "current"),
    ("単純平均", "mean"),
    ("加重平均のみ", "wavg"),
    ("中央値", "median"),
    ("中央値55+ベスト45", "median_best"),
]


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

    original = predictor.aggregate_speed_score
    try:
        for scope, subset in (("学習", train), ("検証", test)):
            print(f"\n=== {scope} ===")
            print(f"{'集約方法':<22} {'◎勝率':>7} {'◎複勝率':>7} {'単回収':>7} {'複回収':>7}")
            for label, mode in MODES:
                predictor.aggregate_speed_score = (
                    original if mode == "current" else make_aggregator(mode)
                )
                m = evaluate_weights(precompute(subset, variants), WEIGHTS)
                print(f"{label:<22} {m['win_rate']*100:>6.1f}% "
                      f"{m['place_rate']*100:>6.1f}%"
                      f" {m['roi']:>6.1f}% {m['place_roi']:>6.1f}%")
    finally:
        predictor.aggregate_speed_score = original
    return 0


if __name__ == "__main__":
    sys.exit(main())
