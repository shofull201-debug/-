"""コマンドラインインターフェース。

使い方:
    keiba predict data/sample_race.json
    keiba predict race.json --weights speed=0.5,workout=0.3,pedigree=0.2
    keiba speed-index --course 東京 --surface 芝 --distance 1600 \
        --time 93.5 --weight 57 --going 良 --race-class 2勝
    keiba build-base-times results.csv -o my_base_times.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict

from .models import PastRace, RaceCard
from .predictor import predict
from .speed_index import nishida_speed_index


def _parse_weights(text: str) -> dict[str, float]:
    weights = {}
    for part in text.split(","):
        key, _, value = part.partition("=")
        key = key.strip()
        if key not in ("speed", "workout", "pedigree"):
            raise argparse.ArgumentTypeError(
                f"不明な重みキー: {key}（speed / workout / pedigree のいずれか）"
            )
        weights[key] = float(value)
    return weights


def cmd_predict(args: argparse.Namespace) -> int:
    with open(args.race_file, encoding="utf-8") as f:
        card = RaceCard.from_dict(json.load(f))

    results = predict(card, weights=args.weights)

    race = card.race
    print(f"\n=== {race.name or '予想'} ===")
    print(f"{race.course} {race.surface}{race.distance}m {race.race_class} 馬場:{race.going}\n")

    if args.json:
        payload = [
            {
                "rank": r.rank,
                "mark": r.mark,
                "horse_number": r.horse_number,
                "name": r.name,
                "total": r.total,
                "deviations": r.deviations,
                "speed_score": r.speed_score,
                "speed_indices": r.speed_indices,
                "pedigree": r.pedigree,
                "workout": r.workout,
            }
            for r in results
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    header = f"{'印':<2} {'馬番':>3} {'馬名':<12} {'総合':>6} {'速偏':>5} {'調偏':>5} {'血偏':>5}  過去5走指数(直近→)"
    print(header)
    print("-" * len(header))
    for r in results:
        num = str(r.horse_number) if r.horse_number is not None else "-"
        indices = " ".join(f"{v:.0f}" for v in r.speed_indices) or "（初出走）"
        print(
            f"{r.mark or '　':<2} {num:>3} {r.name:<12} {r.total:>6.1f}"
            f" {r.deviations['speed']:>5.1f} {r.deviations['workout']:>5.1f}"
            f" {r.deviations['pedigree']:>5.1f}  {indices}"
        )

    print("\n凡例: 総合=偏差値の加重合成 / 速偏=スピード指数偏差値 / 調偏=追切偏差値 / 血偏=血統偏差値")
    return 0


def cmd_speed_index(args: argparse.Namespace) -> int:
    past = PastRace(
        date="",
        course=args.course,
        surface=args.surface,
        distance=args.distance,
        going=args.going,
        time_sec=args.time,
        weight_carried=args.weight,
        finish_position=0,
        field_size=0,
        race_class=args.race_class,
        track_variant=args.track_variant,
    )
    index = nishida_speed_index(past)
    print(f"西田式スピード指数: {index:.1f}")
    return 0


def cmd_build_base_times(args: argparse.Namespace) -> int:
    """過去レース結果 CSV から基準タイム表を再構築する。

    CSV ヘッダー: course,surface,distance,race_class,going,time_sec
    良馬場のレースのみ集計し、クラス補正を戻して 1勝クラス相当に正規化した
    平均タイムを基準タイムとする。
    """
    from .speed_index import _load_base_times

    offsets = _load_base_times()["class_offsets"]
    buckets: dict[str, list[float]] = defaultdict(list)

    with open(args.results_csv, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("going", "良") != "良":
                continue
            distance = int(row["distance"])
            offset = offsets.get(row.get("race_class", "1勝"), 0.0) * (distance / 1600)
            normalized = float(row["time_sec"]) - offset
            buckets[f"{row['course']}|{row['surface']}|{distance}"].append(normalized)

    base_times = {
        key: round(sum(times) / len(times), 1)
        for key, times in sorted(buckets.items())
        if len(times) >= args.min_samples
    }
    output = {
        "class_offsets": offsets,
        "fallback": _load_base_times()["fallback"],
        "base_times": base_times,
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"{len(base_times)} 条件の基準タイムを {args.output} に書き出しました")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="keiba",
        description="競馬予想システム — 血統・追切・西田式スピード指数による総合評価",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_predict = sub.add_parser("predict", help="レースカード JSON から予想を出力")
    p_predict.add_argument("race_file", help="レースカード JSON ファイル")
    p_predict.add_argument(
        "--weights",
        type=_parse_weights,
        default=None,
        help="重み指定（例: speed=0.5,workout=0.3,pedigree=0.2）",
    )
    p_predict.add_argument("--json", action="store_true", help="JSON 形式で出力")
    p_predict.set_defaults(func=cmd_predict)

    p_index = sub.add_parser("speed-index", help="単発でスピード指数を計算")
    p_index.add_argument("--course", required=True, help="競馬場名（例: 東京）")
    p_index.add_argument("--surface", required=True, choices=["芝", "ダ"])
    p_index.add_argument("--distance", type=int, required=True)
    p_index.add_argument("--time", type=float, required=True, help="走破タイム（秒）")
    p_index.add_argument("--weight", type=float, default=56.0, help="斤量")
    p_index.add_argument("--going", default="良", choices=["良", "稍重", "重", "不良"])
    p_index.add_argument("--race-class", default="1勝", help="クラス（例: 2勝, OP, G1）")
    p_index.add_argument("--track-variant", type=float, default=None, help="馬場指数（実測値）")
    p_index.set_defaults(func=cmd_speed_index)

    p_build = sub.add_parser("build-base-times", help="レース結果 CSV から基準タイム表を構築")
    p_build.add_argument("results_csv", help="CSV: course,surface,distance,race_class,going,time_sec")
    p_build.add_argument("-o", "--output", default="base_times.json")
    p_build.add_argument("--min-samples", type=int, default=5, help="採用する最小サンプル数")
    p_build.set_defaults(func=cmd_build_base_times)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
