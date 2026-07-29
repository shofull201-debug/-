"""「今回背負う斤量」補正の効果検証。

現行モデルは過去走の斤量は55kg基準に補正するが、今回の斤量差は未使用。
スピード生スコアから (今回斤量−55)×2pt×K を引く補正を加えて、
学習(2022-24)/検証(2025-26)で的中率・回収率が改善するかを測る。
K=1.0 が西田式の換算(1kg=2pt)どおり。

使い方:
    python scripts/tune_today_impost.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import keiba.backtest as bt  # noqa: E402
from keiba.backtest import evaluate_weights, precompute  # noqa: E402
from keiba.predictor import evaluate_horse as original_eval  # noqa: E402
from keiba.scrape.dataset import load_dataset  # noqa: E402
from keiba.track_variant import VariantTable  # noqa: E402

WEIGHTS = {"speed": 0.5, "pedigree": 0.1, "connections": 0.2, "style": 0,
           "going": 0.1}
KS = [0.0, 0.5, 1.0, 2.0]


def main() -> int:
    ds = load_dataset("data/dataset_2022_2026_full.json.gz")
    races = [r for r in ds["races"] if r["race"]["date"] >= "2022-07-01"]
    train = {"races": [r for r in races if r["race"]["date"] < "2025-01-01"]}
    test = {"races": [r for r in races if r["race"]["date"] >= "2025-01-01"]}
    variants = VariantTable.load("data/track_variants_2022_2026.json")

    for k in KS:
        def patched(horse, surface, distance, course="", _k=k):
            r = original_eval(horse, surface, distance, course=course)
            if r["speed_indices"]:  # 過去走なしはそのまま(欠損扱い)
                r["speed"] -= _k * (horse.weight_carried - 55.0) * 2.0
            return r

        bt.evaluate_horse = patched
        print(f"\n=== 今回斤量補正 K={k}(1kgあたり{k*2:.0f}pt) ===")
        for name, subset in (("学習", train), ("検証", test)):
            m = evaluate_weights(precompute(subset, variants), WEIGHTS)
            print(f"  {name}: ◎勝率 {m['win_rate']*100:5.1f}%"
                  f" / 複勝率 {m['place_rate']*100:5.1f}%"
                  f" / 単回収 {m['roi']:5.1f}% / 複回収 {m['place_roi']:5.1f}%")
    bt.evaluate_horse = original_eval
    return 0


if __name__ == "__main__":
    sys.exit(main())
