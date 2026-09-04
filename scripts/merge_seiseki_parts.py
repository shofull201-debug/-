"""TARGET出力の3分割CSVを1本の成績CSV(seseki2形式)へ結合する。

TARGETの出力項目上限などで成績が複数ファイルに分かれた場合に、
(日付, 開催, Ｒ, 馬名S) をキーに横結合して convert_seiseki2.py が
読める形式へ揃える。

想定する3ファイル:
  A: 斤量・馬体重・上り3F・通過順・種牡馬/母父馬 を含むもの
  B: 単勝配当・複勝配当・連系配当 を含むもの
  C: 走破タイム・芝ダ・PCI を含むもの
どの列がどのファイルにあるかは問わず、同名列は先に現れたファイルを優先する。

馬番は3ファイルとも持たないため、レース内の出現順で連番を振る。
モデルは枠順・馬番を予想に使わない(検証済みで不採用)ため、
払戻を馬に対応づけるキーとしてのみ機能すれば足りる。

使い方:
    python scripts/merge_seiseki_parts.py A.csv B.csv C.csv -o merged.csv
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from pathlib import Path

KEY = ("日付", "開催", "Ｒ", "馬名S")


def read_rows(path: str) -> list[dict]:
    text = Path(path).read_bytes().decode("cp932", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csvs", nargs="+", help="結合する成績CSV(2本以上)")
    ap.add_argument("-o", "--output", required=True)
    args = ap.parse_args()

    merged: dict[tuple, dict] = {}
    order: list[tuple] = []
    for path in args.csvs:
        rows = read_rows(path)
        missing = [k for k in KEY if k not in (rows[0] if rows else {})]
        if missing:
            print(f"エラー: {path} に結合キーがありません: {missing}")
            return 1
        for row in rows:
            key = tuple(row[k].strip() for k in KEY)
            if key not in merged:
                merged[key] = {}
                order.append(key)
            target = merged[key]
            for col, val in row.items():
                # 同名列は先勝ち。ただし空欄なら後から埋める
                if col and (col not in target or not target[col].strip()):
                    target[col] = val or ""
        print(f"{path}: {len(rows)} 行 / 列 {len(rows[0]) if rows else 0}")

    # 馬名S → 馬名、レース内連番 → 馬番 を補う
    seq: dict[tuple, int] = {}
    for key in order:
        race = key[:3]
        seq[race] = seq.get(race, 0) + 1
        merged[key]["馬名"] = key[3]
        merged[key]["馬番"] = str(seq[race])
        # 距離が「芝2600」形式のファイルがあるため、馬場種別を剥がして数値に揃える
        dist = (merged[key].get("距離") or "").strip()
        m = re.match(r"^([芝ダ障])\s*(\d+)$", dist)
        if m:
            merged[key]["距離"] = m.group(2)
            if not (merged[key].get("芝・ダ") or "").strip():
                merged[key]["芝・ダ"] = m.group(1)

    cols: list[str] = []
    for key in order:
        for col in merged[key]:
            if col not in cols:
                cols.append(col)

    with open(args.output, "w", encoding="cp932", errors="replace", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for key in order:
            w.writerow({c: merged[key].get(c, "") for c in cols})

    print(f"\n{args.output}: {len(order)} 行 / 列 {len(cols)} に結合しました")
    need = ["馬名", "着順", "走破タイム", "開催", "芝・ダ", "距離", "日付", "レース名",
            "Ｒ", "馬場状態", "馬番", "単勝配当", "複勝配当", "斤量", "騎手", "調教師",
            "人気", "馬体重", "馬体重増減", "PCI", "4角", "3角", "頭数", "上り3F"]
    lack = [c for c in need if c not in cols]
    print("変換に必要な列:", "すべて揃いました" if not lack else f"不足 {lack}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
