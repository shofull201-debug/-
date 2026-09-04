"""バリュー検証: 指数順位×単勝オッズ帯ごとの回収率マトリクスを作る。

「指数は高評価なのにオッズがつく」領域にプラス圏があるかを調べる。
過適合を避けるため、前半期間で有望セルを選び、後半期間で追試する。

- 単勝回収: オッズ×100円(的中時)で厳密に計算できる(全馬にオッズあり)
- 複勝回収: 3着内馬の複勝払戻を使用

使い方:
    python scripts/value_analysis.py data/dataset_2022_2026_v3.json.gz \
        --variants data/track_variants_2022_2026.json
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

WEIGHTS = {"speed": 0.5, "workout": 0.0, "pedigree": 0.2, "going": 0.0, "style": 0.1}

ODDS_BANDS = [(1.0, 3.0, "〜3倍"), (3.0, 5.0, "3〜5倍"), (5.0, 10.0, "5〜10倍"),
              (10.0, 20.0, "10〜20倍"), (20.0, 50.0, "20〜50倍"), (50.0, 1e9, "50倍〜")]
RANK_BANDS = [(1, 1, "指数1位"), (2, 2, "2位"), (3, 3, "3位"), (4, 5, "4-5位")]


def odds_band(odds: float) -> str | None:
    for lo, hi, label in ODDS_BANDS:
        if lo <= odds < hi:
            return label
    return None


def rank_band(rank: int) -> str | None:
    for lo, hi, label in RANK_BANDS:
        if lo <= rank <= hi:
            return label
    return None


def collect(races: list[dict], precomp) -> list[dict]:
    """全出走馬の (指数順位, オッズ, 結果, 払戻) を平坦化する。"""
    bets = []
    for race, pre in zip(races, precomp):
        totals = [
            sum(dev[k] * w for k, w in WEIGHTS.items() if k in dev)
            for dev in pre.deviations
        ]
        order = sorted(range(len(totals)), key=lambda i: totals[i], reverse=True)
        for rank, i in enumerate(order, start=1):
            odds = pre.odds[i]
            if odds is None:
                continue
            fin = pre.finish[i]
            bets.append({
                "date": race["race"]["date"],
                "rank": rank,
                "odds": odds,
                "win": fin == 1,
                "tan_ret": odds * 100 if fin == 1 else 0,
                "fuku_ret": (pre.place_pay[i] or 0) if (fin or 99) <= 3 else 0,
            })
    return bets


def matrix(bets: list[dict]) -> dict:
    cells = defaultdict(lambda: {"n": 0, "win": 0, "tan": 0.0, "fuku": 0.0})
    for b in bets:
        rb, ob = rank_band(b["rank"]), odds_band(b["odds"])
        if rb is None or ob is None:
            continue
        c = cells[(rb, ob)]
        c["n"] += 1
        c["win"] += b["win"]
        c["tan"] += b["tan_ret"]
        c["fuku"] += b["fuku_ret"]
    return cells


def print_matrix(cells: dict, metric: str) -> None:
    head = "単勝回収%" if metric == "tan" else "複勝回収%"
    print(f"  [{head}] (件数)")
    print(f"  {'':<8}" + "".join(f" {label:>12}" for _, _, label in ODDS_BANDS))
    for _, _, rl in RANK_BANDS:
        row = [f"  {rl:<8}"]
        for _, _, ol in ODDS_BANDS:
            c = cells.get((rl, ol))
            if not c or c["n"] < 30:
                row.append(f" {'-':>12}")
            else:
                roi = c[metric] / (c["n"] * 100) * 100
                row.append(f" {roi:>5.0f}({c['n']:>5})")
        print("".join(row))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dataset")
    ap.add_argument("--variants")
    ap.add_argument("--split", default="2025-01-01", help="学習/検証の分割日")
    ap.add_argument("--min-date", default="2022-07-01")
    args = ap.parse_args()

    ds = load_dataset(args.dataset)
    races = [r for r in ds["races"] if r["race"]["date"] >= args.min_date]
    variants = VariantTable.load(args.variants) if args.variants else None
    print(f"対象 {len(races)} レース / 重み {WEIGHTS}")
    precomp = precompute({"races": races}, variants)
    bets = collect(races, precomp)

    train = [b for b in bets if b["date"] < args.split]
    test = [b for b in bets if b["date"] >= args.split]
    m_train, m_test = matrix(train), matrix(test)

    for name, m in ((f"学習 ({args.min_date}〜{args.split})", m_train),
                    (f"検証 ({args.split}〜)", m_test)):
        print(f"\n=== {name} ===")
        print_matrix(m, "tan")
        print_matrix(m, "fuku")

    # 学習期間で単勝回収100%超のセルを検証期間で追試
    print("\n=== 学習でプラスのセル → 検証での成績 ===")
    for key, c in sorted(m_train.items()):
        if c["n"] < 100:
            continue
        train_roi = c["tan"] / (c["n"] * 100) * 100
        if train_roi < 100:
            continue
        t = m_test.get(key)
        t_roi = t["tan"] / (t["n"] * 100) * 100 if t and t["n"] else float("nan")
        print(f"  {key[0]}×{key[1]}: 学習 {train_roi:.0f}% ({c['n']}件)"
              f" → 検証 {t_roi:.0f}% ({t['n'] if t else 0}件)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
