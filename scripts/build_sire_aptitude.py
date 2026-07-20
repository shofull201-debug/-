"""血統リスト(kettou.txt)×実走データから種牡馬適性表を自動構築する。

- kettou.txt: TARGET出力の「馬名 性 齢 父 母 調教師」固定幅テキスト(cp932)
- データセット(2022-2026)の全走と父を結合し、産駒の着順率から
  芝ダ・距離カテゴリ・道悪の適性(0-10)を推定する

スコア変換: 産駒の平均着順率 perf = mean(1 - (着順-1)/(頭数-1)) を
  score = 5 + (perf - 0.5) × 25 を1〜10にクリップ
(perf 0.50=平均的→5、0.58→7、0.62→8 程度の感覚)

手作業で調整済みの種牡馬(既存エントリ)は上書きしない。
父名は9文字で切れている場合があるため、既存表に一意の前方一致があれば
その完全名に正規化する。

使い方:
    python scripts/build_sire_aptitude.py kettou.txt -o src/keiba/data/sire_aptitude.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from keiba.going_aptitude import WET_GOINGS  # noqa: E402
from keiba.pedigree import distance_category  # noqa: E402
from keiba.scrape.dataset import load_dataset  # noqa: E402

MIN_SURFACE = 30   # 芝/ダ適性に必要な最小出走数
MIN_DIST = 20      # 距離カテゴリ適性の最小出走数
MIN_WET = 15       # 道悪適性の最小出走数
MIN_TOTAL = 15     # 表に採用する種牡馬の最小総出走数
PRIOR_RUNS = 30    # ベイズ縮小: 平均0.5の仮想走数(少数サンプルを中立へ寄せる)


def perf_to_score(values: list[float]) -> int:
    """perf のリストをスコア(1-10)へ。仮想 PRIOR_RUNS 走(perf 0.5)を混ぜて縮小する。"""
    perf = (sum(values) + 0.5 * PRIOR_RUNS) / (len(values) + PRIOR_RUNS)
    return max(1, min(10, round(5 + (perf - 0.5) * 25)))


def load_sire_map(kettou_path: str, known_sires: set[str]) -> dict[str, str]:
    """馬名→父。9文字切れの父名は既存表への一意前方一致で完全名へ正規化。"""
    sire_map = {}
    for line in open(kettou_path, encoding="cp932"):
        toks = line.split()
        if len(toks) < 5:
            continue
        sire = toks[3]
        if sire not in known_sires:
            matches = [s for s in known_sires if s.startswith(sire)]
            if len(matches) == 1:
                sire = matches[0]
        sire_map[toks[0]] = sire
    return sire_map


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("kettou", help="kettou.txt (馬名 性 齢 父 母 調教師)")
    ap.add_argument("--datasets", nargs="*",
                    default=["data/dataset_2022_2025.json.gz", "data/dataset_2026.json.gz"])
    ap.add_argument("-o", "--output", default="src/keiba/data/sire_aptitude.json")
    ap.add_argument("--sire-map-out", default="data/sire_map.json.gz",
                    help="馬名→父の対応表の保存先(カード構築で再利用)")
    args = ap.parse_args()

    table = json.load(open(args.output, encoding="utf-8"))
    known = set(table["sires"])
    sire_map = load_sire_map(args.kettou, known)
    print(f"血統マップ: {len(sire_map)} 頭")

    # 産駒の走を父ごとに集計
    stats = defaultdict(lambda: defaultdict(list))
    matched = 0
    for path in args.datasets:
        for rd in load_dataset(path)["races"]:
            info = rd["race"]
            n = len(rd["horses"])
            if n < 2:
                continue
            for h in rd["horses"]:
                sire = sire_map.get(h["name"])
                if sire is None:
                    continue
                matched += 1
                perf = 1 - (h["result"]["finish_position"] - 1) / (n - 1)
                s = stats[sire]
                s["total"].append(perf)
                s[f"surface:{info['surface']}"].append(perf)
                s[f"dist:{distance_category(info['distance'])}"].append(perf)
                if info["going"] in WET_GOINGS or info["going"] in ("稍", "不"):
                    s["wet"].append(perf)
    print(f"父と紐づいた走: {matched}")

    def score(values: list, minimum: int, fallback: int) -> int:
        if len(values) < minimum:
            return fallback
        return perf_to_score(values)

    added = 0
    hand_tuned = {s for s in known if not table["sires"][s].get("_auto")}
    for sire, s in stats.items():
        # 手作業調整済みは保護、_auto エントリは再実行で更新
        if sire in hand_tuned or len(s["total"]) < MIN_TOTAL:
            continue
        overall = perf_to_score(s["total"])
        table["sires"][sire] = {
            "surface": {
                "芝": score(s["surface:芝"], MIN_SURFACE, overall),
                "ダ": score(s["surface:ダ"], MIN_SURFACE, overall),
            },
            "distance": {
                cat: score(s[f"dist:{cat}"], MIN_DIST, overall)
                for cat in ("短距離", "マイル", "中距離", "長距離")
            },
            "wet": score(s["wet"], MIN_WET, overall),
            "_auto": True,
            "_starts": len(s["total"]),
        }
        added += 1

    base_comment = table.get("_comment", "").split(" / _auto=", 1)[0]
    table["_comment"] = (
        base_comment
        + f" / _auto=True のエントリは産駒成績({matched}走)からの自動構築"
        f"(perf→5+(perf-0.5)*25、仮想{PRIOR_RUNS}走で縮小、最小{MIN_TOTAL}走)"
    )
    json.dump(table, open(args.output, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"自動構築で {added} 種牡馬を追加 → 計 {len(table['sires'])} 頭")

    from keiba.scrape.dataset import save_dataset
    save_dataset({"sire_map": sire_map}, args.sire_map_out)
    print(f"馬名→父の対応表を {args.sire_map_out} に保存")
    return 0


if __name__ == "__main__":
    sys.exit(main())
