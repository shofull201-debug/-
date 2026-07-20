"""スピード指数の集約方式(ベスト比率×大敗トリム)を実配当バックテストで比較する。

現行: 鮮度×関連度の加重平均 55% + ベスト指数 45%(blend_best=0.45, trim_worst=0)。
候補ごとに backtest.precompute を回し、同じ重みで ◎の勝率・複勝率・
単勝/複勝回収率を測る。

評価は 2025-07-01 以降のレースに限定する(データセットは 2025-01 開始で、
序盤は各馬の past_races が浅く、集約方式の差が出ないため)。

使い方:
    python scripts/tune_speed_aggregation.py data/dataset_2025_2026.json.gz \
        --variants data/track_variants_2022_2026.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from keiba import speed_index  # noqa: E402
from keiba.backtest import evaluate_weights, precompute  # noqa: E402
from keiba.scrape.dataset import load_dataset  # noqa: E402
from keiba.track_variant import VariantTable  # noqa: E402

# (ラベル, blend_best, trim_worst)
CONFIGS = [
    ("現行: 平均55%+ベスト45%",        0.45, 0),
    ("ベスト重視60%",                  0.60, 0),
    ("ベスト重視75%",                  0.75, 0),
    ("ベストのみ",                     1.00, 0),
    ("大敗1走トリム",                  0.45, 1),
    ("大敗1走トリム+ベスト60%",        0.60, 1),
    ("大敗2走トリム",                  0.45, 2),
]

WEIGHT_SETS = {
    "デフォルト重み": {"speed": 0.5, "workout": 0.0, "pedigree": 0.2, "going": 0.0, "style": 0.1},
    "ROI最適重み":    {"speed": 0.5, "workout": 0.0, "pedigree": 0.3, "going": 0.1, "style": 0.1},
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dataset")
    ap.add_argument("--variants")
    ap.add_argument("--min-date", default="2025-07-01",
                    help="この日付以降のレースで評価(履歴の浅い序盤を除外)")
    args = ap.parse_args()

    dataset = load_dataset(args.dataset)
    races = [r for r in dataset["races"] if r["race"]["date"] >= args.min_date]
    subset = {"races": races}
    print(f"評価対象: {len(races)} レース ({args.min_date} 以降)")
    variants = VariantTable.load(args.variants) if args.variants else None

    for wname, weights in WEIGHT_SETS.items():
        print(f"\n=== {wname} {weights} ===")
        print(f"{'集約方式':<24} {'◎勝率':>7} {'◎複勝率':>7} {'単回収':>7} {'複回収':>7}")
        for label, blend, trim in CONFIGS:
            speed_index.BLEND_BEST = blend
            speed_index.TRIM_WORST = trim
            m = evaluate_weights(precompute(subset, variants), weights)
            print(f"{label:<24} {m['win_rate']*100:>6.1f}% {m['place_rate']*100:>6.1f}%"
                  f" {m['roi']:>6.1f}% {m['place_roi']:>6.1f}%")
    speed_index.BLEND_BEST = 0.45
    speed_index.TRIM_WORST = 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
