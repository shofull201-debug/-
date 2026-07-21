"""当日馬場バイアス補正の係数を学習期間で調整し、検証期間で追試する。

各レースについて、同じ日・開催・馬場種別の「それより前のレース」から
前残り度を推定し、脚質偏差値に (推定バイアス − 全体平均) × 方向 × 係数 を
加えて◎の成績がどう変わるかを測る。当日の最初の2レースなどは推定不能なので
無補正のまま(実運用と同じ条件)。

使い方:
    python scripts/tune_track_bias.py data/dataset_2022_2026_full.json.gz \
        --variants data/track_variants_2022_2026.json
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from keiba.backtest import evaluate_weights, precompute  # noqa: E402
from keiba.models import HorseEntry  # noqa: E402
from keiba.scrape.dataset import load_dataset  # noqa: E402
from keiba.track_bias import STYLE_DIRECTION, race_front_bias  # noqa: E402
from keiba.running_style import infer_style  # noqa: E402
from keiba.track_variant import VariantTable  # noqa: E402

WEIGHTS = {"speed": 0.5, "workout": 0.0, "pedigree": 0.2, "going": 0.0, "style": 0.1}
COEFS = [0.0, 10.0, 20.0, 40.0]  # バイアス乖離1.0あたりの脚質偏差値ポイント
MIN_PRIOR_RACES = 5


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dataset")
    ap.add_argument("--variants")
    ap.add_argument("--split", default="2025-01-01")
    ap.add_argument("--min-date", default="2022-07-01")
    args = ap.parse_args()

    ds = load_dataset(args.dataset)
    races = [r for r in ds["races"] if r["race"]["date"] >= args.min_date]
    variants = VariantTable.load(args.variants) if args.variants else None
    precomp = precompute({"races": races}, variants)

    # 同日・同開催・同馬場種別ごとにレース番号順へ
    groups = defaultdict(list)
    for idx, race in enumerate(races):
        date, kaisai, rno = race["race"]["race_id"].split("|")
        groups[(date, kaisai, race["race"]["surface"])].append((int(rno), idx))

    biases = [race_front_bias(r) for r in races]
    known = [b for b in biases if b is not None]
    base = sum(known) / len(known)
    print(f"前残り度の全体平均: {base:+.3f} (計測 {len(known)} レース)")

    # 各レースの「先行レースからの推定バイアス乖離」
    est = [None] * len(races)
    for key, entries in groups.items():
        entries.sort()
        seen = []
        for _, idx in entries:
            if len(seen) >= MIN_PRIOR_RACES:
                est[idx] = sum(seen) / len(seen) - base
            if biases[idx] is not None:
                seen.append(biases[idx])
    n_est = sum(1 for e in est if e is not None)
    print(f"バイアス推定可能: {n_est}/{len(races)} レース")

    # 各馬の脚質方向
    directions = []
    for race in races:
        dirs = []
        for h in race["horses"]:
            style, _ = infer_style(HorseEntry.from_dict(h))
            dirs.append(STYLE_DIRECTION.get(style or "", 0.0))
        directions.append(dirs)

    def adjusted(coef: float):
        out = []
        for pre, e, dirs in zip(precomp, est, directions):
            if not coef or e is None:
                out.append(pre)
                continue
            devs = [
                {**d, "style": d["style"] + coef * e * dr}
                for d, dr in zip(pre.deviations, dirs)
            ]
            out.append(replace(pre, deviations=devs))
        return out

    is_test = [r["race"]["date"] >= args.split for r in races]
    for name, mask in (("学習", [not t for t in is_test]), ("検証", is_test)):
        print(f"\n=== {name} ===")
        print(f"{'係数':>4} {'◎勝率':>7} {'◎複勝率':>7} {'単回収':>7} {'複回収':>7}")
        for coef in COEFS:
            adj = adjusted(coef)
            subset = [p for p, m in zip(adj, mask) if m]
            m = evaluate_weights(subset, WEIGHTS)
            print(f"{coef:>4.0f} {m['win_rate']*100:>6.1f}% {m['place_rate']*100:>6.1f}%"
                  f" {m['roi']:>6.1f}% {m['place_roi']:>6.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
