"""TARGET調教CSVを調教索引に変換する。

対応フォーマット(どちらも cp932、複数ファイル可):
1. 坂路好タイム(ヘッダあり):
   場所,年月日,曜日,時刻,馬名,Ｃ,性別,年齢,収得賞金,調教師,
   Time1..Time4(4F/3F/2F/1F通し),Lap4..Lap1
2. ウッドチップ調教一覧(ヘッダなし40列):
   場所,コース記号,回り,年月日,曜日,時刻,馬名,Ｃ,性別,年齢,収得賞金,調教師,
   10F..1F通し(10列),Lap...(9列),血統番号,日付,場所,父,母,...
   → 6F(なければ5F/7F/4F)の通しで採用。遅い帯同・キャンター
     (1Fあたり15秒超)は追い切りとみなさず除外

出力: data/workout_index.json.gz
  {"workouts": {馬名: [[ISO日付, 施設, 通し, 終い1F, コース, ハロン数], ...]}}

使い方:
    python scripts/convert_tyoukyo.py t2.csv 今週コース.csv -o data/workout_index.json.gz
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from keiba.scrape.dataset import save_dataset  # noqa: E402

# 妥当な範囲(計測異常・キャンターの除外): 1Fあたり11〜15秒、終い1F 10〜18秒
LAST1F_RANGE = (10.0, 18.0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csvs", nargs="+", help="調教CSV(追加分も並べて渡せる)")
    ap.add_argument("-o", "--output", default="data/workout_index.json.gz")
    ap.add_argument("--merge", action="store_true",
                    help="出力先の既存索引に追記する(重複は除去)")
    ap.add_argument("--fast-quantile", type=float, default=None,
                    help="その日・その施設で速い方から指定割合だけ残す"
                         "(例 0.2)。全量の調教一覧から「好タイム」相当の"
                         "母集団を作るために使う")
    args = ap.parse_args()

    index: dict[str, list] = defaultdict(list)
    if args.merge and Path(args.output).exists():
        from keiba.scrape.dataset import load_dataset

        for name, works in load_dataset(args.output)["workouts"].items():
            index[name] = list(works)
        print(f"既存索引を読み込み: {len(index)} 頭")
    kept = skipped = 0

    def add(name, ymd, facility, total, last, course, furlongs):
        nonlocal kept, skipped
        if course == "坂路":
            ok = total is not None and 45.0 <= total <= 75.0  # 追切効果の検証時と同じ範囲
        else:
            ok = total is not None and 11.0 <= total / furlongs <= 15.0  # キャンター除外
        if (not name or len(ymd) != 8 or not ok or last is None
                or not LAST1F_RANGE[0] <= last <= LAST1F_RANGE[1]):
            skipped += 1
            return
        iso = f"{ymd[0:4]}-{ymd[4:6]}-{ymd[6:8]}"
        index[name].append([iso, facility, total, last, course, furlongs])
        kept += 1

    def to_f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    for path in args.csvs:
        # 未登録の外国産馬など、まれに文字化けした行があるため replace で読み飛ばす
        with open(path, encoding="cp932", errors="replace", newline="") as f:
            first = f.readline()
            f.seek(0)
            if "馬名" in first:  # 坂路(ヘッダあり)
                for row in csv.DictReader(f):
                    add((row.get("馬名") or "").strip(),
                        (row.get("年月日") or "").strip(),
                        (row.get("場所") or "").strip(),
                        to_f(row.get("Time1")), to_f(row.get("Time4")),
                        "坂路", 4)
            elif len(first.split(",")) == 18:  # 坂路(ヘッダなし18列)
                for row in csv.reader(f):
                    if len(row) < 14:
                        skipped += 1
                        continue
                    add(row[4].strip(), row[1].strip(), row[0].strip(),
                        to_f(row[10]), to_f(row[13]), "坂路", 4)
            else:  # ウッドチップ調教一覧(ヘッダなし40列)
                for row in csv.reader(f):
                    if len(row) < 31:
                        skipped += 1
                        continue
                    times = [to_f(v) for v in row[12:22]]  # 10F..1F 通し
                    total = furlongs = None
                    for fur in (6, 5, 7, 4):               # 6F優先で採用
                        t = times[10 - fur]
                        if t is not None:
                            total, furlongs = t, fur
                            break
                    if total is None:
                        skipped += 1
                        continue
                    add(row[6].strip(), row[3].strip(), row[0].strip(),
                        total, times[9], "W", furlongs)

    for name, works in index.items():
        # 4要素の旧形式は坂路4Fとして正規化し、重複を除いて日付順に
        normed = [w if len(w) >= 6 else w + ["坂路", 4] for w in works]
        index[name] = sorted({tuple(w) for w in normed})
        index[name] = [list(w) for w in index[name]]

    if args.fast_quantile:
        # 全量の一覧にはキャンター相当の遅い時計が大量に含まれる。予想側の
        # 追切評価は「好タイムのみ」の母集団(載らない馬は欠損扱い)を前提に
        # 作られているため、その日・その施設の速い順で足切りして揃える。
        import bisect

        byday: dict = defaultdict(list)
        for works in index.values():
            for w in works:
                byday[(w[0], w[1], w[4])].append(w[2])
        for v in byday.values():
            v.sort()
        before = sum(len(v) for v in index.values())
        for name in list(index):
            keep = [w for w in index[name]
                    if bisect.bisect_left(byday[(w[0], w[1], w[4])], w[2])
                    / len(byday[(w[0], w[1], w[4])]) <= args.fast_quantile]
            if keep:
                index[name] = keep
            else:
                del index[name]
        after = sum(len(v) for v in index.values())
        print(f"速い方 {args.fast_quantile:.0%} に限定: {before} → {after} 本")
    dates = sorted(w[0] for works in index.values() for w in works)
    print(f"取り込み {kept} 本 / 除外 {skipped} 本 / {len(index)} 頭"
          f" ({dates[0]} 〜 {dates[-1]})")
    save_dataset({"workouts": dict(index)}, args.output)
    print(f"{args.output} に保存しました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
