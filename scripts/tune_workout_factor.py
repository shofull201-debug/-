"""調教好タイム索引を使い、追切要素の効果を初めて実測する。

対象は索引がカバーする期間のレース。各出走馬にレース前21日以内の
坂路好タイム(直近+ベスト、最大2本)を付与し、追切重みを変えて
◎の成績を比較する。期間を前半/後半に割り、効果の一貫性も確認する。

使い方:
    python scripts/tune_workout_factor.py data/dataset_2022_2026_v3.json.gz \
        --variants data/track_variants_2022_2026.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from keiba.backtest import evaluate_weights, precompute  # noqa: E402
from keiba.scrape.dataset import load_dataset  # noqa: E402
from keiba.track_variant import VariantTable  # noqa: E402
from keiba.workout_attach import attach_to_card  # noqa: E402

BASE = {"speed": 0.5, "pedigree": 0.2, "going": 0.0, "style": 0.1,
        "connections": 0.1}
WORKOUT_WEIGHTS = [0.0, 0.1, 0.2, 0.3]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dataset")
    ap.add_argument("--variants")
    ap.add_argument("--index", default="data/workout_index.json.gz")
    ap.add_argument("--min-date", default="2025-08-13")
    ap.add_argument("--max-date", default="2026-07-12")
    ap.add_argument("--mid-date", default="2026-02-01", help="前半/後半の分割日")
    args = ap.parse_args()

    index = load_dataset(args.index)["workouts"]
    ds = load_dataset(args.dataset)
    races = [r for r in ds["races"]
             if args.min_date <= r["race"]["date"] <= args.max_date]
    attached = sum(attach_to_card(r, index) for r in races)
    n_horses = sum(len(r["horses"]) for r in races)
    print(f"対象 {len(races)} レース / 追切付与 {attached}/{n_horses} 頭"
          f" ({attached/n_horses*100:.1f}%)")

    variants = VariantTable.load(args.variants) if args.variants else None
    precomp = precompute({"races": races}, variants)

    halves = {
        "前半": [r["race"]["date"] < args.mid_date for r in races],
        "後半": [r["race"]["date"] >= args.mid_date for r in races],
        "全体": [True] * len(races),
    }
    for name, mask in halves.items():
        print(f"\n=== {name} ({sum(mask)}レース) ===")
        print(f"{'追切重み':>6} {'◎勝率':>7} {'◎複勝率':>7} {'単回収':>7} {'複回収':>7}")
        for w in WORKOUT_WEIGHTS:
            weights = dict(BASE, workout=w)
            subset = [p for p, m in zip(precomp, mask) if m]
            m = evaluate_weights(subset, weights)
            print(f"{w:>6.2f} {m['win_rate']*100:>6.1f}% {m['place_rate']*100:>6.1f}%"
                  f" {m['roi']:>6.1f}% {m['place_roi']:>6.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
