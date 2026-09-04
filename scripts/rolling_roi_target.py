"""単勝回収率100%を狙う購入フィルタのローリング検証。

「全◎を買う」ではなく「条件を満たす◎だけ買う」戦略を、
rolling_walk_forward.py と同じプロトコルで検証する:
前年までのデータでROI最大の条件を選び、翌年でその条件のROIを測る。

試す戦略ファミリー:
  A) オッズ帯     — ◎のオッズが帯[lo,hi)のときだけ買う
  B) 自信度ギャップ — ◎と○の総合点差がg以上のときだけ買う
  C) 期待値       — 訓練期間で「点差バケツ別の◎勝率」を測り、
                    勝率×オッズ ≥ τ のときだけ買う
  D) B×A複合     — ギャップ+最低オッズ

過適合防止: 訓練期間で最低ベット数を満たす条件のみ採用候補にする。
評価は的中率ではなくROIそのもの(ユーザー要望)。

使い方:
    python scripts/rolling_roi_target.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import keiba.connections as conn_mod  # noqa: E402
import keiba.going_aptitude as going_mod  # noqa: E402
import keiba.pedigree as ped_mod  # noqa: E402
import keiba.speed_index as si_mod  # noqa: E402
from keiba.backtest import precompute  # noqa: E402
from keiba.cli import _result_rows_from_dataset  # noqa: E402
from keiba.scrape.dataset import load_dataset  # noqa: E402
from keiba.track_variant import VariantTable, compute_variants  # noqa: E402
from walk_forward_audit import (  # noqa: E402
    build_asof_base_times,
    build_asof_connections,
    build_asof_sires,
)

FACTORS = ("speed", "pedigree", "connections", "style", "going")
WEIGHTS = {"speed": .5, "pedigree": .1, "connections": .2, "style": 0, "going": .1}
FOLDS = [("2022", "2023"), ("2023", "2024"), ("2024", "2025"), ("2025", "2026")]

MIN_TRAIN_BETS = 300   # 訓練期間でこれ未満しか該当しない条件は候補から外す
ODDS_BANDS = [(1.0, 2.0), (1.0, 3.0), (2.0, 3.0), (2.0, 5.0), (3.0, 5.0),
              (3.0, 10.0), (5.0, 10.0), (5.0, 999.0), (10.0, 999.0), (1.0, 999.0)]
GAPS = [0.0, 1.0, 2.0, 3.0, 5.0, 8.0]
EV_TAUS = [0.8, 1.0, 1.2, 1.5]
GAP_BUCKETS = [0.0, 1.0, 2.0, 3.0, 5.0, 8.0, 99.0]


def build_rows(precomp) -> np.ndarray:
    """レースごとに (◎のオッズ, 単勝払戻, 1着か, 点差) を並べた行列を返す。"""
    rng = np.random.default_rng(0)
    w = np.array([WEIGHTS[f] for f in FACTORS])
    rows = []
    for p in precomp:
        idx = [i for i, f in enumerate(p.finish) if f is not None]
        if len(idx) < 3:
            continue
        perm = rng.permutation(len(idx))
        idx = [idx[i] for i in perm]
        dev = np.array([[p.deviations[i][f] for f in FACTORS] for i in idx])
        totals = dev @ w
        order = np.argsort(-totals)
        top, second = idx[order[0]], idx[order[1]]
        gap = totals[order[0]] - totals[order[1]]
        odds = p.odds[top]
        pay = p.win_pay[top] or 0
        if odds is None:
            # オッズ欠損は払戻から復元(的中時のみ可能)。不可なら除外
            if pay:
                odds = pay / 100.0
            else:
                continue
        rows.append((odds, pay, 1.0 if p.finish[top] == 1 else 0.0, gap))
    return np.array(rows)


def roi(rows: np.ndarray, mask: np.ndarray) -> tuple[float, int, float]:
    """(回収率%, ベット数, 的中率%)"""
    n = int(mask.sum())
    if n == 0:
        return 0.0, 0, 0.0
    return rows[mask, 1].sum() / n, n, rows[mask, 2].mean() * 100


def pick_best(cands: list[tuple[str, np.ndarray, np.ndarray]], rows_tr) -> tuple:
    """訓練ROI最大の条件を返す。cands = [(名前, 訓練mask, テストmask)]"""
    best = None
    for name, m_tr, m_te in cands:
        r, n, _ = roi(rows_tr, m_tr)
        if n < MIN_TRAIN_BETS:
            continue
        if best is None or r > best[1]:
            best = (name, r, m_te)
    return best


def main() -> int:
    ds = load_dataset("data/dataset_2022_2026_v3.json.gz")
    cur_base = json.load(open("src/keiba/data/base_times.json", encoding="utf-8"))
    cur_sires = json.load(open("src/keiba/data/sire_aptitude.json", encoding="utf-8"))
    variants = VariantTable(compute_variants(_result_rows_from_dataset(ds)))

    pooled: dict[str, list[tuple[float, int]]] = {}
    for train_end, test_year in FOLDS:
        train = [r for r in ds["races"] if r["race"]["date"][:4] <= train_end]
        test = [r for r in ds["races"] if r["race"]["date"][:4] == test_year]

        si_mod._load_base_times = (lambda t: (lambda: t))(
            build_asof_base_times(train, cur_base))
        sires = build_asof_sires(train, cur_sires)
        ped_mod._load_sire_data = (lambda t: (lambda: t))(sires)
        going_mod._load_sire_data = (lambda t: (lambda: t))(sires)
        conn_mod._load_default = (lambda t: (lambda: t))(
            build_asof_connections(train))

        rows_tr = build_rows(precompute({"races": train}, variants))
        rows_te = build_rows(precompute({"races": test}, variants))
        print(f"\n=== 〜{train_end}で条件選択 → {test_year}でテスト"
              f" (訓練{len(rows_tr)}R/テスト{len(rows_te)}R) ===", flush=True)

        strategies: dict[str, list[tuple[str, np.ndarray, np.ndarray]]] = {
            "A:オッズ帯": [
                (f"{lo}≤オッズ<{hi}",
                 (rows_tr[:, 0] >= lo) & (rows_tr[:, 0] < hi),
                 (rows_te[:, 0] >= lo) & (rows_te[:, 0] < hi))
                for lo, hi in ODDS_BANDS
            ],
            "B:点差": [
                (f"点差≥{g}", rows_tr[:, 3] >= g, rows_te[:, 3] >= g)
                for g in GAPS
            ],
            "D:点差×最低オッズ": [
                (f"点差≥{g}かつオッズ≥{lo}",
                 (rows_tr[:, 3] >= g) & (rows_tr[:, 0] >= lo),
                 (rows_te[:, 3] >= g) & (rows_te[:, 0] >= lo))
                for g in (1.0, 2.0, 3.0, 5.0) for lo in (1.5, 2.0, 3.0, 5.0)
            ],
        }
        # C: 期待値 — 点差バケツ別◎勝率(訓練)×オッズ
        p_by_bucket = {}
        bucket_tr = np.digitize(rows_tr[:, 3], GAP_BUCKETS) - 1
        bucket_te = np.digitize(rows_te[:, 3], GAP_BUCKETS) - 1
        for b in range(len(GAP_BUCKETS) - 1):
            m = bucket_tr == b
            p_by_bucket[b] = rows_tr[m, 2].mean() if m.sum() >= 50 else np.nan
        ev_tr = np.array([p_by_bucket.get(b, np.nan) for b in bucket_tr]) * rows_tr[:, 0]
        ev_te = np.array([p_by_bucket.get(b, np.nan) for b in bucket_te]) * rows_te[:, 0]
        strategies["C:期待値"] = [
            (f"EV≥{t}", ev_tr >= t, ev_te >= t) for t in EV_TAUS
        ]

        base_r, base_n, base_h = roi(rows_te, np.ones(len(rows_te), dtype=bool))
        print(f"  基準(全◎購入)          : テスト単回収 {base_r:5.1f}%"
              f" ({base_n}R, 的中{base_h:.1f}%)")
        pooled.setdefault("基準(全◎)", []).append((base_r, base_n))

        for fam, cands in strategies.items():
            best = pick_best(cands, rows_tr)
            if best is None:
                print(f"  {fam}: 候補なし")
                continue
            name, r_tr, m_te = best
            r_te, n_te, h_te = roi(rows_te, m_te)
            print(f"  {fam:<12} 選択={name:<18} 訓練 {r_tr:5.1f}%"
                  f" → テスト {r_te:5.1f}% ({n_te}R, 的中{h_te:.1f}%)")
            pooled.setdefault(fam, []).append((r_te, n_te))

    print("\n===== 4年通算(完全アウトオブサンプル・ベット数加重) =====")
    for fam, results in pooled.items():
        n = sum(x[1] for x in results)
        r = sum(x[0] * x[1] for x in results) / n if n else 0.0
        yearly = " / ".join(f"{x[0]:.0f}%" for x in results)
        print(f"  {fam:<14}: 単回収 {r:5.1f}% ({n}ベット) [年別: {yearly}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
