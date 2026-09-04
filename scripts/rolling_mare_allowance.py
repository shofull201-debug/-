"""牝馬の牡馬換算(性別重量補正)のローリング検証。

牝馬は混合戦で2kgの減量恩恵があるため、過去走指数が牡馬より低めに出る。
「牝馬の過去走指数に A kg × 2pt を加算(=牡馬換算)」した場合の効果を、
rolling_walk_forward.py と同じプロトコル(前年までで A を選び翌年でテスト)
で測る。指数の斤量正規化は線形なので、牝馬の集約スピード生スコアに
A×2pt を一様加算するのと等価。

牝馬限定戦では全馬が同額シフトし偏差値が変わらないため、
全レースに加えて「牡牝混合レース」だけの集計も出す。

性別はTARGET成績CSV(性別列)から馬名→性別マップを構築して付与する。

使い方:
    python scripts/rolling_mare_allowance.py
"""

from __future__ import annotations

import csv
import io
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
from keiba.going_aptitude import is_wet  # noqa: E402
from keiba.models import HorseEntry  # noqa: E402
from keiba.predictor import _to_deviation, evaluate_horse  # noqa: E402
from keiba.scrape.dataset import load_dataset  # noqa: E402
from keiba.track_variant import VariantTable, compute_variants  # noqa: E402
from rolling_walk_forward import evaluate, to_arrays  # noqa: E402
from walk_forward_audit import (  # noqa: E402
    build_asof_base_times,
    build_asof_connections,
    build_asof_sires,
)

WEIGHTS = {"speed": .5, "pedigree": .1, "connections": .2, "style": 0, "going": .1}
ALLOWANCES = [0.0, 1.0, 2.0, 3.0]  # 牡馬換算のkg数(2.0が現実の減量恩恵)
FOLDS = [("2022", "2023"), ("2023", "2024"), ("2024", "2025"), ("2025", "2026")]

UPLOAD_DIR = Path("/root/.claude/uploads/b68432e0-dc3b-5a60-9850-94a840d79dbb")
SEISEKI_FILES = [
    ("276b9372-seiseki.csv", 8, 10),   # (ファイル, 馬名列, 性別列) 35列形式
    ("5442b228-seiseki4.csv", 8, 10),
    ("2aef77a5-seseki2.csv", 5, 7),    # 50列形式
    ("91b68524-seiseki3.csv", 5, 7),
]


def build_sex_map() -> dict[str, str]:
    sex_map: dict[str, str] = {}
    for fname, name_col, sex_col in SEISEKI_FILES:
        path = UPLOAD_DIR / fname
        if not path.exists():
            print(f"  警告: {fname} が見つかりません(スキップ)")
            continue
        text = path.read_bytes().decode("cp932", errors="replace")
        for row in csv.reader(io.StringIO(text)):
            if len(row) <= max(name_col, sex_col):
                continue
            name, sex = row[name_col].strip(), row[sex_col].strip()
            if sex in ("牡", "牝", "セ") and name:
                sex_map[name] = sex
    return sex_map


def precompute_raw(races, variants, sex_map):
    out = []
    for race_data in races:
        info = race_data["race"]
        horses = [HorseEntry.from_dict(h) for h in race_data["horses"]]
        variants.apply_to_horses(horses)
        raws = [evaluate_horse(h, info["surface"], info["distance"],
                               course=info.get("course", "")) for h in horses]
        mare = [1.0 if sex_map.get(h.name) == "牝" else 0.0 for h in horses]
        known = [sex_map.get(h.name) is not None for h in horses]
        wet = is_wet(info.get("going", "良"))
        out.append({
            "speed0": [r["speed"] for r in raws],
            "has_idx": [bool(r["speed_indices"]) for r in raws],
            "mare": mare,
            "mixed": 0.0 < sum(mare) < sum(known),  # 牡牝混合か(性別既知の中で)
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


def arrays_for_a(raw, a: float, mixed_only=False):
    precomp = []
    for r in raw:
        if mixed_only and not r["mixed"]:
            continue
        dev_speed = _to_deviation([
            s + a * 2.0 * m * (1.0 if hi else 0.0)
            for s, m, hi in zip(r["speed0"], r["mare"], r["has_idx"])
        ])
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
    sex_map = build_sex_map()
    print(f"性別マップ: {len(sex_map)}頭 (牝 {sum(1 for s in sex_map.values() if s=='牝')})")

    ds = load_dataset("data/dataset_2022_2026_v3.json.gz")
    all_names = {h["name"] for r in ds["races"] for h in r["horses"]}
    cov = sum(1 for n in all_names if n in sex_map) / len(all_names) * 100
    print(f"データセット出走馬のカバー率: {cov:.1f}%")

    cur_base = json.load(open("src/keiba/data/base_times.json", encoding="utf-8"))
    cur_sires = json.load(open("src/keiba/data/sire_aptitude.json", encoding="utf-8"))
    variants = VariantTable(compute_variants(_result_rows_from_dataset(ds)))

    pooled = {"chosen": [], "a0": [], "a2": []}
    pooled_mixed = {"a0": [], "a2": []}
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

        raw_train = precompute_raw(train, variants, sex_map)
        raw_test = precompute_raw(test, variants, sex_map)
        n_mixed = sum(1 for r in raw_test if r["mixed"])

        print(f"\n=== 〜{train_end}で調整 → {test_year}でテスト"
              f" ({len(raw_test)}R, うち混合戦 {n_mixed}R) ===", flush=True)
        print(f"{'換算kg':>5} {'調整:複勝率':>9} {'テスト:◎勝率':>9} {'複勝率':>7}"
              f" {'単回収':>7} {'複回収':>7} {'混合戦のみ複勝率':>10}")
        best_a, best_key, per_a, per_a_mixed = None, (-1.0, -1.0), {}, {}
        for a in ALLOWANCES:
            m_tr = evaluate(arrays_for_a(raw_train, a), WEIGHTS)
            m_te = evaluate(arrays_for_a(raw_test, a), WEIGHTS)
            m_mx = evaluate(arrays_for_a(raw_test, a, mixed_only=True), WEIGHTS)
            per_a[a], per_a_mixed[a] = m_te, m_mx
            key = (m_tr["place"], m_tr["win"])
            if key > best_key:
                best_key, best_a = key, a
            print(f"{a:>5.1f} {m_tr['place']*100:>8.1f}% {m_te['win']*100:>8.1f}%"
                  f" {m_te['place']*100:>6.1f}% {m_te['tan']:>6.1f}%"
                  f" {m_te['fuku']:>6.1f}% {m_mx['place']*100:>9.1f}%")
        print(f"  → 調整期間で選ばれた換算: {best_a}kg")
        pooled["chosen"].append(per_a[best_a])
        pooled["a0"].append(per_a[0.0])
        pooled["a2"].append(per_a[2.0])
        pooled_mixed["a0"].append(per_a_mixed[0.0])
        pooled_mixed["a2"].append(per_a_mixed[2.0])

    def pool(ms):
        n = sum(m["n"] for m in ms)
        return {kk: sum(m[kk] * m["n"] for m in ms) / n
                for kk in ("win", "place", "tan", "fuku")} | {"n": n}

    print(f"\n===== 4年通算(完全アウトオブサンプル) =====")
    for label, key in (("毎年選んだ換算", "chosen"), ("換算なし(現行)", "a0"),
                       ("牡馬換算2kg固定", "a2")):
        p = pool(pooled[key])
        print(f"  {label:<12}: ◎勝率 {p['win']*100:5.1f}% / 複勝率 {p['place']*100:5.1f}%"
              f" / 単回収 {p['tan']:5.1f}% / 複回収 {p['fuku']:5.1f}%")
    print("  --- 牡牝混合レースのみ ---")
    for label, key in (("換算なし(現行)", "a0"), ("牡馬換算2kg固定", "a2")):
        p = pool(pooled_mixed[key])
        print(f"  {label:<12}: ◎勝率 {p['win']*100:5.1f}% / 複勝率 {p['place']*100:5.1f}%"
              f" / 単回収 {p['tan']:5.1f}% / 複回収 {p['fuku']:5.1f}% ({p['n']}R)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
