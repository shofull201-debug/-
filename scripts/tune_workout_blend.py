"""追切スコアの「最終追い切り偏重」を検証する。

現行は 最終追い切り×0.7 + 期間内ベスト×0.3 のブレンド。
「1週前に強く追って最終は軽く」という王道パターンの馬は最終の時計が
平凡になり不利になる。ベスト側を重視する配点に変えたとき、◎の
的中率・回収率がどう動くかを索引カバー期間の実レースで測る。

注意: 調教索引には脚色情報がない(全て馬なり扱い)ため、ここで測れるのは
「時計ベースで直近を重視するか、期間内ベストを重視するか」の比較。

使い方:
    python scripts/tune_workout_blend.py data/dataset_2022_2026_full.json.gz \
        --variants data/track_variants_2022_2026.json
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from keiba.backtest import _to_deviation, evaluate_weights, precompute  # noqa: E402
from keiba.models import Workout  # noqa: E402
from keiba.scrape.dataset import load_dataset  # noqa: E402
from keiba.track_variant import VariantTable  # noqa: E402
from keiba.workout import score_single_workout  # noqa: E402
from keiba.workout_attach import attach_to_card  # noqa: E402

# ローリングWFで採用した現行重み
WEIGHTS = {"speed": 0.5, "workout": 0.2, "pedigree": 0.1, "style": 0.0,
           "connections": 0.2, "going": 0.0}


def blend_score(workouts: list[Workout], latest_w: float | str) -> float:
    """workout_score のブレンド比率を可変にした版。latest_w='max' で良い方採用。"""
    if not workouts:
        return 50.0
    latest = score_single_workout(workouts[0])
    others = [score_single_workout(w) for w in workouts[1:]]
    if not others:
        return latest
    best = max(others)
    if latest_w == "max":
        return max(latest, best)
    return latest * latest_w + best * (1 - latest_w)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dataset")
    ap.add_argument("--variants")
    ap.add_argument("--index", default="data/workout_index.json.gz")
    ap.add_argument("--min-date", default="2025-08-13")
    ap.add_argument("--max-date", default="2026-07-12")
    ap.add_argument("--mid-date", default="2026-02-01")
    args = ap.parse_args()

    index = load_dataset(args.index)["workouts"]
    ds = load_dataset(args.dataset)
    races = [r for r in ds["races"]
             if args.min_date <= r["race"]["date"] <= args.max_date]
    attached = sum(attach_to_card(r, index) for r in races)
    n_horses = sum(len(r["horses"]) for r in races)
    two = sum(1 for r in races for h in r["horses"]
              if len(h.get("workouts") or []) >= 2)
    print(f"対象 {len(races)} レース / 追切付与 {attached}/{n_horses} 頭"
          f" ({attached/n_horses*100:.1f}%) / うち直近+ベスト2本 {two} 頭")

    variants = VariantTable.load(args.variants) if args.variants else None
    precomp = precompute({"races": races}, variants)

    # 馬ごとの追切1本毎スコアを前計算(ブレンドだけ差し替えられるように)
    per_race_workouts = [
        [[Workout.from_dict(w) for w in (h.get("workouts") or [])]
         for h in r["horses"]]
        for r in races
    ]

    blends: list[tuple[str, float | str]] = [
        ("最終のみ 1.0", 1.0),
        ("現行 0.7", 0.7),
        ("半々 0.5", 0.5),
        ("ベスト重視 0.3", 0.3),
        ("ベストのみ 0.0", 0.0),
        ("良い方max", "max"),
    ]

    halves = {
        "前半": [r["race"]["date"] < args.mid_date for r in races],
        "後半": [r["race"]["date"] >= args.mid_date for r in races],
        "全体": [True] * len(races),
    }
    for name, mask in halves.items():
        print(f"\n=== {name} ({sum(mask)}レース) ===")
        print(f"{'ブレンド':>10} {'◎勝率':>7} {'◎複勝率':>7} {'単回収':>7} {'複回収':>7}")
        for label, lw in blends:
            rebuilt = []
            for pc, wlists, m in zip(precomp, per_race_workouts, mask):
                if not m:
                    continue
                devs = _to_deviation([blend_score(ws, lw) for ws in wlists])
                rebuilt.append(replace(pc, deviations=[
                    dict(d, workout=w) for d, w in zip(pc.deviations, devs)
                ]))
            r = evaluate_weights(rebuilt, WEIGHTS)
            print(f"{label:>10} {r['win_rate']*100:>6.1f}% {r['place_rate']*100:>6.1f}%"
                  f" {r['roi']:>6.1f}% {r['place_roi']:>6.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
