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
MARKS = ("◎", "○", "▲", "△", "△")


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
                                 "tan_ret": 0, "fuku_ret": 0, "fuku_n": 0,
                                 "cover3": 0, "box3": 0})
    # 馬券戦略: (ラベル, 点数, 的中判定, 使う配当キー)
    # 判定は (top2set, top3set, marks_idx) を受け取る
    bets = {
        "馬連◎-○(1点)":   (1,  lambda t2, t3, m: t2 == set(m[:2]), "quinella"),
        "馬連◎流し(4点)":  (4,  lambda t2, t3, m: m[0] in t2 and (t2 - {m[0]}) <= set(m[1:5]), "quinella"),
        "馬連5頭BOX(10点)": (10, lambda t2, t3, m: t2 <= set(m[:5]), "quinella"),
        "3連複5頭BOX(10点)": (10, lambda t2, t3, m: t3 <= set(m[:5]), "trio"),
    }
    bet_stats = defaultdict(lambda: defaultdict(lambda: {"hit": 0, "ret": 0, "cost": 0}))
    wide_hit = defaultdict(int)  # 印5頭BOXでワイドが最低1点当たる(3着内2頭以上)
    lines = []
    for race, pre in zip(races, precomp):
        info = race["race"]
        order = rank_horses(pre.deviations, weights)
        top = order[0]
        finish_top = pre.finish[top]
        grade = info["race_class"]
        top5 = set(order[:5])
        placers = {i for i, f in enumerate(pre.finish) if (f or 99) <= 3}
        for key in (grade, "全重賞"):
            s = stats[key]
            s["n"] += 1
            s["win"] += finish_top == 1
            s["place"] += (finish_top or 99) <= 3
            s["tan_ret"] += pre.win_pay[top] or 0
            if pre.place_pay[top] is not None or True:
                s["fuku_n"] += 1
                s["fuku_ret"] += pre.place_pay[top] or 0
            s["cover3"] += len(top5 & placers)      # 上位5頭中の3着内頭数
            s["box3"] += placers <= top5            # 3着内を全て上位5頭で覆えたか

        top2 = {i for i, f in enumerate(pre.finish) if (f or 99) <= 2}
        payouts = race.get("payouts") or {}
        for key in (grade, "全重賞"):
            for label, (points, is_hit, pay_key) in bets.items():
                b = bet_stats[key][label]
                b["cost"] += points * 100
                if is_hit(top2, placers, order) and payouts.get(pay_key):
                    b["hit"] += 1
                    b["ret"] += payouts[pay_key]
            wide_hit[key] += len(top5 & placers) >= 2

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

    print(f"\n{'':<6} {'レース数':>5} {'◎勝率':>7} {'◎複勝率':>7} {'単回収':>7} {'複回収':>7}"
          f" {'印5頭中3着内':>9} {'3連複BOX率':>8}")
    for key in ("全重賞",) + GRADES:
        s = stats[key]
        if not s["n"]:
            continue
        print(f"{key:<6} {s['n']:>5} {s['win']/s['n']*100:>6.1f}% "
              f"{s['place']/s['n']*100:>6.1f}% "
              f"{s['tan_ret']/s['n']:>6.1f}% {s['fuku_ret']/s['fuku_n']:>6.1f}%"
              f" {s['cover3']/s['n']:>8.2f}頭 {s['box3']/s['n']*100:>7.1f}%")

    print("\n=== 馬券シミュレーション(100円/点) ===")
    print(f"{'':<6}" + "".join(f" {label:>16}" for label in bets)
          + f" {'ワイド1点以上*':>12}")
    for key in ("全重賞",) + GRADES:
        if not stats[key]["n"]:
            continue
        cells = []
        for label in bets:
            b = bet_stats[key][label]
            roi = b["ret"] / b["cost"] * 100 if b["cost"] else 0.0
            cells.append(f" 的中{b['hit']/stats[key]['n']*100:>5.1f}%/回収{roi:>6.1f}%")
        print(f"{key:<6}" + "".join(cells)
              + f" {wide_hit[key]/stats[key]['n']*100:>10.1f}%")
    print("* ワイドは配当列がデータに無いため的中率のみ(印5頭中2頭が3着内)")

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
