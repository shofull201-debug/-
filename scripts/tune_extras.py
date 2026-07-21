"""追加要素(騎手・調教師 / 枠順 / 馬体重増減 / 展開)の効果検証。

各要素をメンバー内偏差値化し、総合点に小さい重み(0.05/0.10)で混ぜて
◎の成績変化を測る。統計系の要素(騎手・調教師・枠順)は学習期間の
成績だけから構築し、検証期間へ持ち越して評価する(リーク防止)。

使い方:
    python scripts/tune_extras.py data/dataset_2022_2026_full.json.gz \
        --variants data/track_variants_2022_2026.json
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from keiba.backtest import precompute  # noqa: E402
from keiba.models import HorseEntry  # noqa: E402
from keiba.predictor import _to_deviation  # noqa: E402
from keiba.running_style import infer_style  # noqa: E402
from keiba.scrape.dataset import load_dataset  # noqa: E402
from keiba.track_variant import VariantTable  # noqa: E402

WEIGHTS = {"speed": 0.5, "workout": 0.0, "pedigree": 0.2, "going": 0.0, "style": 0.1}
EXTRA_WEIGHTS = (0.05, 0.10)
SHRINK_JT = 50      # 騎手・調教師の複勝率のベイズ縮小(仮想騎乗数)
SHRINK_WAKU = 200   # 枠順効果の縮小
BASE_PLACE = 0.25   # 複勝率の事前値


def dist_bin(distance: int) -> str:
    if distance <= 1400:
        return "短"
    if distance <= 1800:
        return "マ"
    return "中長"


def waku_band(number: int, field: int) -> str:
    ratio = (number - 1) / max(1, field - 1)
    return "内" if ratio < 1 / 3 else ("中" if ratio < 2 / 3 else "外")


def build_stats(train_races: list[dict]):
    """学習期間から騎手・調教師・枠順の成績統計を作る。"""
    jt = {"j": defaultdict(lambda: [0, 0]), "t": defaultdict(lambda: [0, 0])}
    waku = defaultdict(lambda: [0.0, 0])
    for race in train_races:
        info = race["race"]
        n = len(race["horses"])
        for h in race["horses"]:
            fin = h["result"]["finish_position"]
            place = 1 if (fin or 99) <= 3 else 0
            for key, name in (("j", h.get("jockey")), ("t", h.get("trainer"))):
                if name:
                    jt[key][name][0] += place
                    jt[key][name][1] += 1
            if h.get("horse_number") and n >= 8:
                perf = 1 - (fin - 1) / (n - 1)
                k = (info["course"], info["surface"], dist_bin(info["distance"]),
                     waku_band(h["horse_number"], n))
                waku[k][0] += perf
                waku[k][1] += 1
    return jt, waku


def shrunk_rate(hits: int, n: int, k: int) -> float:
    return (hits + BASE_PLACE * k) / (n + k)


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
    train_races = [r for r in races if r["race"]["date"] < args.split]
    jt, waku = build_stats(train_races)
    print(f"統計: 騎手{len(jt['j'])}人 / 調教師{len(jt['t'])}人 / 枠セル{len(waku)}")

    # 各レースの基礎総合点
    base_totals = [
        [sum(d[k] * w for k, w in WEIGHTS.items() if k in d) for d in pre.deviations]
        for pre in precomp
    ]

    # 要素ごとの生スコア(レース内で偏差値化して使う)
    def connections(race):
        out = []
        for h in race["horses"]:
            j = jt["j"].get(h.get("jockey") or "")
            t = jt["t"].get(h.get("trainer") or "")
            jr = shrunk_rate(j[0], j[1], SHRINK_JT) if j else BASE_PLACE
            tr = shrunk_rate(t[0], t[1], SHRINK_JT) if t else BASE_PLACE
            out.append((jr * 0.6 + tr * 0.4) * 100)
        return out

    def waku_score(race):
        info = race["race"]
        n = len(race["horses"])
        out = []
        for h in race["horses"]:
            num = h.get("horse_number")
            if not num or n < 8:
                out.append(50.0)
                continue
            k = (info["course"], info["surface"], dist_bin(info["distance"]),
                 waku_band(num, n))
            s, cnt = waku.get(k, (0.0, 0))
            adj = (s / cnt - 0.5) * cnt / (cnt + SHRINK_WAKU) if cnt else 0.0
            out.append(50.0 + adj * 100)
        return out

    def weight_swing(race):
        out = []
        for h in race["horses"]:
            diff = h["result"].get("weight_diff")
            out.append(50.0 - max(0, abs(diff) - 8) * 5 if diff is not None else 50.0)
        return out

    def pace_fit(race):
        styles = []
        for h in race["horses"]:
            style, _ = infer_style(HorseEntry.from_dict(h))
            styles.append(style)
        n_front = sum(1.0 if s == "逃げ" else 0.5 if s == "先行" else 0.0
                      for s in styles)
        dirs = {"逃げ": 1.0, "先行": 0.5, "差し": -0.5, "追込": -1.0}
        return [50.0 - 8.0 * dirs.get(s or "", 0.0) * (n_front - 2.5) for s in styles]

    factors = {
        "騎手・調教師": connections,
        "枠順": waku_score,
        "馬体重増減": weight_swing,
        "展開(先行型頭数)": pace_fit,
    }

    is_test = [r["race"]["date"] >= args.split for r in races]

    def evaluate(totals_fn, mask):
        n = wins = places = 0
        tan = fuku = 0.0
        for race, pre, base, m in zip(races, precomp, base_totals, mask):
            if not m:
                continue
            totals = totals_fn(race, base)
            top = max(range(len(totals)), key=totals.__getitem__)
            fin = pre.finish[top]
            n += 1
            wins += fin == 1
            places += (fin or 99) <= 3
            tan += pre.win_pay[top] or 0
            fuku += pre.place_pay[top] or 0
        return (f"◎勝率 {wins/n*100:5.1f}% / 複勝率 {places/n*100:5.1f}%"
                f" / 単回収 {tan/n:5.1f}% / 複回収 {fuku/n:5.1f}%")

    for name, mask in (("学習(統計は自己参照あり)", [not t for t in is_test]),
                       ("検証(統計は学習期間から)", is_test)):
        print(f"\n=== {name} ===")
        print(f"  {'ベースライン':<18}", evaluate(lambda r, b: b, mask))
        for fname, fn in factors.items():
            for w in EXTRA_WEIGHTS:
                def totals_fn(race, base, fn=fn, w=w):
                    dev = _to_deviation(fn(race))
                    return [b * (1 - w) + d * w for b, d in zip(base, dev)]
                print(f"  {fname:<14}w={w:.2f}", evaluate(totals_fn, mask))
    return 0


if __name__ == "__main__":
    sys.exit(main())
