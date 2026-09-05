"""拡張ウォークフォワード最適化。

2022で調整→2023でテスト、〜2023で調整→2024でテスト…を繰り返し、
「毎年、過去データだけで選んだ重み」の翌年成績を繋いで実力値を測る。
参照テーブル(基準タイム・種牡馬適性・騎手統計)も各Foldの調整年まで
のデータのみで再構築する(未来情報ゼロ)。

重み探索: 速度0.5固定(スケール不変のため比率のみ意味を持つ)、
血統/騎手厩舎/脚質/道悪をグリッド探索。選択基準は◎複勝率
(回収率の直接最適化は過適合が実証済みのため使わない)。

使い方:
    python scripts/rolling_walk_forward.py
"""

from __future__ import annotations

import sys
from itertools import product
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

FACTORS = ("speed", "pedigree", "connections", "style", "going", "workout")
GRID = [
    {"speed": 0.5, "pedigree": p, "connections": c, "style": s, "going": g,
     "workout": 0.0}
    for p, c, s, g in product((0, .1, .2, .3), (0, .1, .2), (0, .1), (0, .1, .2))
]
# --workouts 指定時は追切も探索軸に加える(調教索引を張った場合のみ意味を持つ)
GRID_WORK = [
    dict(w, workout=k) for w in GRID for k in (0, .1, .2, .3)
]
BASELINE = {"speed": .5, "pedigree": .2, "connections": .1, "style": 0,
            "going": .1, "workout": 0.0}
# 現行の運用重み(追切0.2は坂路1年分の実測値で、ローリング検証は未実施だった)
CURRENT = {"speed": .5, "pedigree": .1, "connections": .2, "style": 0,
           "going": .1, "workout": .2}
FOLDS = [("2022", "2023"), ("2023", "2024"), ("2024", "2025"), ("2025", "2026")]


def to_arrays(precomp):
    """レースごとの (偏差値行列, 着順, 単勝払戻, 複勝払戻) に変換。

    データセットの馬は着順順に並んでいるため、そのまま argmax すると
    同点時(新馬戦で全馬過去走なし等)に勝ち馬を選ぶリークが起きる。
    レースごとに馬順を固定シードでシャッフルして同点を公平にする。
    """
    rng = np.random.default_rng(0)
    out = []
    for p in precomp:
        n = len(p.deviations)
        perm = rng.permutation(n)
        dev = np.array([[p.deviations[i][f] for f in FACTORS] for i in perm])
        finish = np.array([p.finish[i] if p.finish[i] else 99 for i in perm])
        win = np.array([p.win_pay[i] if p.win_pay[i] else 0 for i in perm])
        plc = np.array([p.place_pay[i] if p.place_pay[i] else 0 for i in perm])
        out.append((dev, finish, win, plc))
    return out


def evaluate(arrays, weights) -> dict:
    w = np.array([weights[f] for f in FACTORS])
    n = wins = places = 0
    tan = fuku = 0
    for dev, finish, win, plc in arrays:
        top = int(np.argmax(dev @ w))
        n += 1
        wins += finish[top] == 1
        places += finish[top] <= 3
        tan += win[top]
        fuku += plc[top]
    return {
        "n": n, "win": wins / n, "place": places / n,
        "tan": tan / n, "fuku": fuku / n,
    }


def main() -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", default="data/dataset_2022_2026_v3.json.gz")
    ap.add_argument("--workouts", nargs="?", const="data/workout_index.json.gz",
                    help="調教索引を各走に張り、追切も重み探索の対象にする")
    args = ap.parse_args()

    ds = load_dataset(args.dataset)
    grid = GRID
    if args.workouts:
        from keiba.workout_attach import attach_to_card

        index = load_dataset(args.workouts)["workouts"]
        applied = sum(attach_to_card(r, index) for r in ds["races"])
        runs = sum(len(r["horses"]) for r in ds["races"])
        print(f"調教索引を {applied}/{runs} 走 ({applied/runs:.1%}) に適用")
        grid = GRID_WORK
    cur_base = json.load(open("src/keiba/data/base_times.json", encoding="utf-8"))
    cur_sires = json.load(open("src/keiba/data/sire_aptitude.json", encoding="utf-8"))

    chosen_seq = []
    pooled_best = []
    pooled_base = []
    for train_end, test_year in FOLDS:
        train = [r for r in ds["races"] if r["race"]["date"][:4] <= train_end]
        test = [r for r in ds["races"] if r["race"]["date"][:4] == test_year]

        # as-of 参照テーブル(調整年まで)
        si_mod._load_base_times = (lambda t: (lambda: t))(
            build_asof_base_times(train, cur_base))
        sires = build_asof_sires(train, cur_sires)
        ped_mod._load_sire_data = (lambda t: (lambda: t))(sires)
        going_mod._load_sire_data = (lambda t: (lambda: t))(sires)
        conn_mod._load_default = (lambda t: (lambda: t))(
            build_asof_connections(train))
        variants = VariantTable(compute_variants(_result_rows_from_dataset(ds)))

        arr_train = to_arrays(precompute({"races": train}, variants))
        arr_test = to_arrays(precompute({"races": test}, variants))

        best_w, best_key = None, (-1.0, -1.0)
        for w in grid:
            m = evaluate(arr_train, w)
            key = (m["place"], m["win"])
            if key > best_key:
                best_key, best_w = key, w
        m_best = evaluate(arr_test, best_w)
        m_base = evaluate(arr_test, CURRENT if args.workouts else BASELINE)
        chosen_seq.append((train_end, test_year, best_w, m_best, m_base))
        pooled_best.append(m_best)
        pooled_base.append(m_base)

        wtxt = "/".join(f"{k[:4]}{v}" for k, v in best_w.items() if k != "speed")
        print(f"\n=== 〜{train_end}で調整 → {test_year}でテスト"
              f" ({m_best['n']}R) ===")
        print(f"  選ばれた重み: 速0.5 {wtxt}")
        print(f"  選択重み : ◎勝率 {m_best['win']*100:5.1f}% / 複勝率 {m_best['place']*100:5.1f}%"
              f" / 単回収 {m_best['tan']:5.1f}% / 複回収 {m_best['fuku']:5.1f}%")
        print(f"  現行構成 : ◎勝率 {m_base['win']*100:5.1f}% / 複勝率 {m_base['place']*100:5.1f}%"
              f" / 単回収 {m_base['tan']:5.1f}% / 複回収 {m_base['fuku']:5.1f}%")

    def pool(ms):
        n = sum(m["n"] for m in ms)
        return {k: sum(m[k] * m["n"] for m in ms) / n for k in ("win", "place", "tan", "fuku")} | {"n": n}

    pb, pc = pool(pooled_best), pool(pooled_base)
    print(f"\n===== 4年通算(完全アウトオブサンプル {pb['n']}R) =====")
    print(f"  毎年再調整 : ◎勝率 {pb['win']*100:5.1f}% / 複勝率 {pb['place']*100:5.1f}%"
          f" / 単回収 {pb['tan']:5.1f}% / 複回収 {pb['fuku']:5.1f}%")
    print(f"  現行固定   : ◎勝率 {pc['win']*100:5.1f}% / 複勝率 {pc['place']*100:5.1f}%"
          f" / 単回収 {pc['tan']:5.1f}% / 複回収 {pc['fuku']:5.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
