"""追切の重みをローリング・ウォークフォワードで検証する。

追切0.2は「坂路好タイム1年分」で決めた実測値で、過去の調教データが
無かったため他の要素と同じローリング検証にかけられていなかった。
調教索引が2022年まで遡れるようになったので、他の重みを現行構成に
固定したまま追切だけを 0 / 0.1 / 0.2 / 0.3 と振って翌年成績を測る。

注意: 索引の追切は脚色(馬なり/一杯)と併せ結果を持たない(全馬「馬なり」
として評価される)。週次運用では記事から脚色つきで手入力しているため、
ここで測っているのは「時計だけの追切評価」の価値である。

使い方:
    python scripts/rolling_workout_weight.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

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
from rolling_walk_forward import FOLDS, evaluate, to_arrays  # noqa: E402
from walk_forward_audit import (  # noqa: E402
    build_asof_base_times,
    build_asof_connections,
    build_asof_sires,
)

# 追切以外は現行の運用重み(速0.5 / 血0.1 / 騎厩0.2 / 脚質0 / 道悪0.1)で固定
BASE = {"speed": .5, "pedigree": .1, "connections": .2, "style": 0, "going": .1}
WORKOUT_WEIGHTS = (0.0, 0.1, 0.2, 0.3, 0.4)


def main() -> int:
    ds = load_dataset("data/dataset_2022_2026_v3.json.gz")
    from keiba.workout_attach import attach_to_card

    index = load_dataset("data/workout_index.json.gz")["workouts"]
    applied = sum(attach_to_card(r, index) for r in ds["races"])
    runs = sum(len(r["horses"]) for r in ds["races"])
    print(f"調教索引を {applied}/{runs} 走 ({applied/runs:.1%}) に適用")

    cur_base = json.load(open("src/keiba/data/base_times.json", encoding="utf-8"))
    cur_sires = json.load(open("src/keiba/data/sire_aptitude.json", encoding="utf-8"))
    variants = VariantTable(compute_variants(_result_rows_from_dataset(ds)))

    pooled: dict = {w: [] for w in WORKOUT_WEIGHTS}
    pooled["chosen"] = []
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

        arr_train = to_arrays(precompute({"races": train}, variants))
        arr_test = to_arrays(precompute({"races": test}, variants))

        print(f"\n=== 〜{train_end}で調整 → {test_year}でテスト"
              f" ({len(arr_test)}R) ===")
        print(f"{'追切':>5} {'調整:複勝率':>10} {'テスト:◎勝率':>11} {'複勝率':>7}"
              f" {'単回収':>7} {'複回収':>7}")
        best_w, best_key = None, (-1.0, -1.0)
        for w in WORKOUT_WEIGHTS:
            weights = dict(BASE, workout=w)
            m_tr = evaluate(arr_train, weights)
            m_te = evaluate(arr_test, weights)
            pooled[w].append(m_te)
            key = (m_tr["place"], m_tr["win"])
            if key > best_key:
                best_key, best_w = key, w
            print(f"{w:>5.1f} {m_tr['place']*100:>9.1f}% {m_te['win']*100:>10.1f}%"
                  f" {m_te['place']*100:>6.1f}% {m_te['tan']:>6.1f}%"
                  f" {m_te['fuku']:>6.1f}%")
        print(f"  → 調整期間で選ばれた追切の重み: {best_w}")
        pooled["chosen"].append(pooled[best_w][-1])

    def pool(ms):
        n = sum(m["n"] for m in ms)
        return {k: sum(m[k] * m["n"] for m in ms) / n
                for k in ("win", "place", "tan", "fuku")} | {"n": n}

    print(f"\n===== 4年通算(完全アウトオブサンプル"
          f" {sum(m['n'] for m in pooled['chosen'])}R) =====")
    for key in list(WORKOUT_WEIGHTS) + ["chosen"]:
        label = ("毎年選んだ重み" if key == "chosen"
                 else f"追切{key}固定" + ("(現行)" if key == 0.2 else ""))
        p = pool(pooled[key])
        print(f"  {label:<16}: ◎勝率 {p['win']*100:5.1f}%"
              f" / 複勝率 {p['place']*100:5.1f}%"
              f" / 単回収 {p['tan']:5.1f}% / 複回収 {p['fuku']:5.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
