"""上がり3F補正係数(AGARI_COEF)を学習期間で調整し、検証期間で追試する。

補正: 過去走の指数に「レース平均との上がり差 × 係数」を加える
(平均より1秒速い上がり → +係数ポイント)。

使い方:
    python scripts/tune_agari.py data/dataset_2022_2026_v3.json.gz \
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

WEIGHTS = {"speed": 0.5, "workout": 0.0, "pedigree": 0.2, "going": 0.0, "style": 0.1}
COEFS = [0.0, 2.0, 4.0, 6.0, 10.0]


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

    for name, subset in (("学習", train), ("検証", test)):
        print(f"\n=== {name} ===")
        print(f"{'係数':>4} {'◎勝率':>7} {'◎複勝率':>7} {'単回収':>7} {'複回収':>7}")
        for coef in COEFS:
            speed_index.AGARI_COEF = coef
            m = evaluate_weights(precompute(subset, variants), WEIGHTS)
            print(f"{coef:>4.0f} {m['win_rate']*100:>6.1f}% {m['place_rate']*100:>6.1f}%"
                  f" {m['roi']:>6.1f}% {m['place_roi']:>6.1f}%")
    speed_index.AGARI_COEF = 0.0
    return 0


if __name__ == "__main__":
    sys.exit(main())
