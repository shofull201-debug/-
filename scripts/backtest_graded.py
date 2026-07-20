"""重賞レースだけを対象にした実配当バックテスト。

データセットから G1/G2/G3 を抽出し、指定重みでの◎○▲の成績
(勝率・複勝率・単複回収率、グレード別内訳、レース別一覧)を出す。

使い方:
    python scripts/backtest_graded.py data/dataset_2025_2026.json.gz \
        --variants data/track_variants_2022_2026.json --min-date 2025-07-13
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from keiba.backtest import precompute  # noqa: E402
from keiba.scrape.dataset import load_dataset  # noqa: E402
from keiba.track_variant import VariantTable  # noqa: E402

GRADES = ("G1", "G2", "G3")
MARKS = ("◎", "○", "▲")


def rank_horses(deviations: list[dict], weights: dict[str, float]) -> list[int]:
    """総合点の高い順に馬のインデックスを返す。"""
    totals = [
        sum(dev[k] * w for k, w in weights.items() if k in dev)
        for dev in deviations
    ]
    return sorted(range(len(totals)), key=lambda i: totals[i], reverse=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dataset")
    ap.add_argument("--variants")
    ap.add_argument("--min-date", default="2025-07-13")
    ap.add_argument("--max-date", default="9999-12-31")
    ap.add_argument("--weights", default="speed=0.5,workout=0,pedigree=0.3,going=0.1,style=0.1")
    ap.add_argument("--report", help="レース別一覧(Markdown)の出力先")
    args = ap.parse_args()

    weights = {}
    for part in args.weights.split(","):
        k, v = part.split("=")
        weights[k.strip()] = float(v)

    dataset = load_dataset(args.dataset)
    races = [
        r for r in dataset["races"]
        if r["race"]["race_class"] in GRADES
        and args.min_date <= r["race"]["date"] <= args.max_date
    ]
    print(f"重賞 {len(races)} レース ({args.min_date}〜、重み {weights})")
    variants = VariantTable.load(args.variants) if args.variants else None
    precomp = precompute({"races": races}, variants)

    stats = defaultdict(lambda: {"n": 0, "win": 0, "place": 0,
                                 "tan_ret": 0, "fuku_ret": 0, "fuku_n": 0})
    lines = []
    for race, pre in zip(races, precomp):
        info = race["race"]
        order = rank_horses(pre.deviations, weights)
        top = order[0]
        finish_top = pre.finish[top]
        grade = info["race_class"]
        for key in (grade, "全重賞"):
            s = stats[key]
            s["n"] += 1
            s["win"] += finish_top == 1
            s["place"] += (finish_top or 99) <= 3
            s["tan_ret"] += pre.win_pay[top] or 0
            if pre.place_pay[top] is not None or True:
                s["fuku_n"] += 1
                s["fuku_ret"] += pre.place_pay[top] or 0

        cells = []
        for mark, idx in zip(MARKS, order):
            h = race["horses"][idx]
            fin = pre.finish[idx]
            odds = pre.odds[idx]
            star = "**" if (fin or 99) <= 3 else ""
            cells.append(f"{mark}{h['name']}({star}{fin}着{star}"
                         f"{'/'+format(odds,'.1f')+'倍' if odds else ''})")
        lines.append(f"| {info['date']} | {grade} | {info['name']} | "
                     + " ".join(cells) + " |")

    print(f"\n{'':<6} {'レース数':>5} {'◎勝率':>7} {'◎複勝率':>7} {'単回収':>7} {'複回収':>7}")
    for key in ("全重賞",) + GRADES:
        s = stats[key]
        if not s["n"]:
            continue
        print(f"{key:<6} {s['n']:>5} {s['win']/s['n']*100:>6.1f}% "
              f"{s['place']/s['n']*100:>6.1f}% "
              f"{s['tan_ret']/s['n']:>6.1f}% {s['fuku_ret']/s['fuku_n']:>6.1f}%")

    if args.report:
        header = (
            f"# 重賞バックテスト ({args.min_date}〜)\n\n"
            f"重み: `{weights}` / 太字 = 3着以内\n\n"
            "| 日付 | 格 | レース | 印(着順/単勝オッズ) |\n|---|---|---|---|\n"
        )
        Path(args.report).write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
        print(f"\nレース別一覧を {args.report} に保存")
    return 0


if __name__ == "__main__":
    sys.exit(main())
