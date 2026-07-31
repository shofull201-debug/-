"""今回斤量補正Kのローリング・ウォークフォワード検証。

rolling_walk_forward.py と同じプロトコルで、補正係数K
(スピード生スコアから (今回斤量-55)×2pt×K を引く。K=1.0が現行=西田式換算)
を「前年までで選び→翌年でテスト」する。参照テーブルも各Foldの
調整年までのデータのみで再構築する(未来情報ゼロ)。

evaluate_horse には K=1.0 が組み込み済みのため、いったん補正を戻した
生スコアと斤量差を保存し、Kだけ差し替えて偏差値を再計算する
(二重補正を避け、全Kを1回の前計算で評価できる)。

使い方:
    python scripts/rolling_today_impost.py
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
from keiba.backtest import PrecompRace  # noqa: E402
from keiba.cli import _result_rows_from_dataset  # noqa: E402
from keiba.models import HorseEntry  # noqa: E402
from keiba.predictor import _to_deviation, evaluate_horse  # noqa: E402
from keiba.scrape.dataset import load_dataset  # noqa: E402
from keiba.track_variant import VariantTable, compute_variants  # noqa: E402
from keiba.going_aptitude import is_wet  # noqa: E402
from rolling_walk_forward import evaluate, to_arrays  # noqa: E402
from walk_forward_audit import (  # noqa: E402
    build_asof_base_times,
    build_asof_connections,
    build_asof_sires,
)

# ローリングWFで採用済みの重み(全Foldで選出された構成)
WEIGHTS = {"speed": .5, "pedigree": .1, "connections": .2, "style": 0, "going": .1}
KS = [0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0]
FOLDS = [("2022", "2023"), ("2023", "2024"), ("2024", "2025"), ("2025", "2026")]


def precompute_raw(races: list[dict], variants) -> list[dict]:
    """バックテスト用前計算のうち、速度だけ生スコア+斤量差で保持する版。"""
    out = []
    for race_data in races:
        info = race_data["race"]
        horses = [HorseEntry.from_dict(h) for h in race_data["horses"]]
        variants.apply_to_horses(horses)
        raws = [evaluate_horse(h, info["surface"], info["distance"],
                               course=info.get("course", "")) for h in horses]
        speed0, imp = [], []
        for h, r in zip(horses, raws):
            if r["speed_indices"]:
                # 組み込みのK=1.0補正を戻して「補正なし生スコア」と斤量差を保存
                d = (h.weight_carried - 55.0) * 2.0
                speed0.append(r["speed"] + d)
                imp.append(d)
            else:
                speed0.append(r["speed"])
                imp.append(0.0)
        wet = is_wet(info.get("going", "良"))
        out.append({
            "speed0": speed0, "imp": imp,
            "ped": _to_deviation([r["pedigree"]["score"] for r in raws]),
            "going": (_to_deviation([r["going_aptitude"]["score"] for r in raws])
                      if wet else [50.0] * len(raws)),
            "style": _to_deviation([r["style"]["score"] for r in raws]),
            "conn": _to_deviation([r["connections"]["score"] for r in raws]),
            "finish": [h.get("result", {}).get("finish_position")
                       for h in race_data["horses"]],
            "win": [((race_data.get("payouts") or {}).get("win") or {}).get(
                str(h.get("horse_number"))) for h in race_data["horses"]],
            "plc": [((race_data.get("payouts") or {}).get("place") or {}).get(
                str(h.get("horse_number"))) for h in race_data["horses"]],
        })
    return out


def arrays_for_k(raw: list[dict], k: float):
    precomp = []
    for r in raw:
        dev_speed = _to_deviation([s - k * d for s, d in zip(r["speed0"], r["imp"])])
        precomp.append(PrecompRace(
            deviations=[
                {"speed": s, "pedigree": p, "workout": 50.0, "going": g,
                 "style": st, "connections": c}
                for s, p, g, st, c in zip(dev_speed, r["ped"], r["going"],
                                          r["style"], r["conn"])
            ],
            finish=r["finish"], odds=[None] * len(dev_speed),
            win_pay=r["win"], place_pay=r["plc"],
        ))
    return to_arrays(precomp)


def main() -> int:
    ds = load_dataset("data/dataset_2022_2026_full.json.gz")
    cur_base = json.load(open("src/keiba/data/base_times.json", encoding="utf-8"))
    cur_sires = json.load(open("src/keiba/data/sire_aptitude.json", encoding="utf-8"))
    variants = VariantTable(compute_variants(_result_rows_from_dataset(ds)))

    pooled = {"chosen": [], "k0": [], "k1": []}
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

        raw_train = precompute_raw(train, variants)
        raw_test = precompute_raw(test, variants)

        print(f"\n=== 〜{train_end}で調整 → {test_year}でテスト"
              f" ({len(raw_test)}R) ===")
        print(f"{'K':>5} {'調整:複勝率':>9} {'テスト:◎勝率':>9} {'複勝率':>7}"
              f" {'単回収':>7} {'複回収':>7}")
        best_k, best_key, per_k = None, (-1.0, -1.0), {}
        for k in KS:
            m_tr = evaluate(arrays_for_k(raw_train, k), WEIGHTS)
            m_te = evaluate(arrays_for_k(raw_test, k), WEIGHTS)
            per_k[k] = m_te
            key = (m_tr["place"], m_tr["win"])
            if key > best_key:
                best_key, best_k = key, k
            print(f"{k:>5.2f} {m_tr['place']*100:>8.1f}% {m_te['win']*100:>8.1f}%"
                  f" {m_te['place']*100:>6.1f}% {m_te['tan']:>6.1f}% {m_te['fuku']:>6.1f}%")
        print(f"  → 調整期間で選ばれたK: {best_k}")
        pooled["chosen"].append(per_k[best_k])
        pooled["k0"].append(per_k[0.0])
        pooled["k1"].append(per_k[1.0])

    def pool(ms):
        n = sum(m["n"] for m in ms)
        return {kk: sum(m[kk] * m["n"] for m in ms) / n
                for kk in ("win", "place", "tan", "fuku")} | {"n": n}

    print(f"\n===== 4年通算(完全アウトオブサンプル"
          f" {sum(m['n'] for m in pooled['chosen'])}R) =====")
    for label, key in (("毎年選んだK", "chosen"), ("K=0固定(補正なし)", "k0"),
                       ("K=1.0固定(現行)", "k1")):
        p = pool(pooled[key])
        print(f"  {label:<14}: ◎勝率 {p['win']*100:5.1f}% / 複勝率 {p['place']*100:5.1f}%"
              f" / 単回収 {p['tan']:5.1f}% / 複回収 {p['fuku']:5.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
