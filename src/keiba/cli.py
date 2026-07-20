"""コマンドラインインターフェース。

使い方:
    keiba predict data/sample_race.json
    keiba predict race.json --weights speed=0.5,workout=0.3,pedigree=0.2
    keiba predict race.json --weights-file weights.json
    keiba speed-index --course 東京 --surface 芝 --distance 1600 \
        --time 93.5 --weight 57 --going 良 --race-class 2勝
    keiba scrape --start 2026-04-01 --end 2026-06-30 -o dataset.json
    keiba build-variants dataset.json -o track_variants.json
    keiba optimize dataset.json --variants track_variants.json -o weights.json
    keiba build-base-times dataset.json -o base_times.json
    keiba predict race.json --variants track_variants.json
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
        if key not in ("speed", "workout", "pedigree", "going", "style"):
            raise argparse.ArgumentTypeError(
                f"不明な重みキー: {key}（speed / workout / pedigree / going / style のいずれか）"
            )
        weights[key] = float(value)
    return weights


def cmd_predict(args: argparse.Namespace) -> int:
    with open(args.race_file, encoding="utf-8") as f:
        card = RaceCard.from_dict(json.load(f))

    weights = args.weights
    if args.weights_file:
        with open(args.weights_file, encoding="utf-8") as f:
            weights = json.load(f)

    if args.variants:
        from .track_variant import VariantTable

        applied = VariantTable.load(args.variants).apply_to_card(card)
        print(f"馬場指数を過去走 {applied} 件に適用しました")

    results = predict(card, weights=weights)

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
                "style": r.style,
                "going_aptitude": r.going_aptitude,
            }
            for r in results
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    wet = results and "going" in results[0].deviations
    # 脚質が1頭でも判明しているときだけ脚質列を表示する
    styled = any(r.style and r.style.get("style") for r in results)
    going_col = f" {'道偏':>5}" if wet else ""
    style_col = f" {'脚偏':>5} {'脚質':<3}" if styled else ""
    header = f"{'印':<2} {'馬番':>3} {'馬名':<12} {'総合':>6} {'速偏':>5} {'調偏':>5} {'血偏':>5}{going_col}{style_col}  過去5走指数(直近→)"
    print(header)
    print("-" * len(header))
    def dev_fmt(value):
        # None は欠損（重みは他要素へ再配分済み）
        return f" {value:>5.1f}" if value is not None else f" {'--':>5}"

    for r in results:
        num = str(r.horse_number) if r.horse_number is not None else "-"
        indices = " ".join(f"{v:.0f}" for v in r.speed_indices) or "（初出走）"
        going_val = dev_fmt(r.deviations["going"]) if wet else ""
        style_val = ""
        if styled:
            style_name = (r.style or {}).get("style") or "－"
            style_val = f"{dev_fmt(r.deviations['style'])} {style_name:<3}"
        print(
            f"{r.mark or '　':<2} {num:>3} {r.name:<12} {r.total:>6.1f}"
            f"{dev_fmt(r.deviations['speed'])}{dev_fmt(r.deviations['workout'])}"
            f"{dev_fmt(r.deviations['pedigree'])}{going_val}{style_val}  {indices}"
        )

    legend = "\n凡例: 総合=偏差値の加重合成 / 速偏=スピード指数偏差値 / 調偏=追切偏差値 / 血偏=血統偏差値 / --=データなし(重みを他要素へ再配分)"
    if wet:
        legend += " / 道偏=道悪適性偏差値"
    if styled:
        legend += " / 脚偏=脚質×コース形態偏差値"
    print(legend)
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


def cmd_scrape(args: argparse.Namespace) -> int:
    from .scrape.dataset import build_dataset, save_dataset
    from .scrape.netkeiba import NetkeibaClient

    client = NetkeibaClient(cache_dir=args.cache_dir, wait_sec=args.wait)
    dataset = build_dataset(
        client,
        start=args.start,
        end=args.end,
        surface=args.surface,
        max_races=args.max_races,
        min_past_races=args.min_past_races,
        results_only=args.results_only,
        checkpoint_path=args.output,  # 開催日ごとに途中保存
    )
    save_dataset(dataset, args.output)
    print(f"{len(dataset['races'])} レースを {args.output} に保存しました")
    if args.results_only:
        print("※ 結果のみモード: build-base-times / build-variants の入力に使えます")
    return 0


def cmd_scrape_jra(args: argparse.Namespace) -> int:
    """JRA 公式の成績ページからレース結果を取得してデータセット形式で保存する。

    引数にはURLのほか、保存済みHTMLファイルのパスも指定できる
    （アクセス制限のある環境でブラウザ保存したページを処理する用途）。
    """
    import os

    from .scrape.jra import parse_jra_result_page, to_dataset_race
    from .scrape.netkeiba import NetkeibaClient, detect_encoding

    client = NetkeibaClient(cache_dir=args.cache_dir, wait_sec=args.wait)
    races = []
    for source in args.sources:
        if os.path.exists(source):
            raw = open(source, "rb").read()
            html = raw.decode(detect_encoding(raw, default="cp932"), errors="replace")
        else:
            html = client.get(source)
        parsed = parse_jra_result_page(html, source)
        if parsed is None:
            print(f"パース失敗（成績表が見つかりません）: {source}")
            continue
        races.append(to_dataset_race(parsed))
        print(
            f"取得: {parsed.date} {parsed.course} {parsed.surface}{parsed.distance}m"
            f" {parsed.race_class} {parsed.name} 馬場:{parsed.going} {len(parsed.rows)}頭"
        )

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump({"races": races}, f, ensure_ascii=False, indent=1)
    print(f"{len(races)} レースを {args.output} に保存しました")
    print("※ build-base-times / build-variants の入力として使えます")
    return 0


def cmd_optimize(args: argparse.Namespace) -> int:
    from .backtest import FACTORS, active_factors, evaluate_weights, grid_search, precompute
    from .predictor import DEFAULT_WEIGHTS

    from .scrape.dataset import load_dataset

    dataset = load_dataset(args.dataset)
    print(f"データセット: {len(dataset['races'])} レース")

    variants = None
    if args.variants:
        from .track_variant import VariantTable

        variants = VariantTable.load(args.variants)
        print(f"馬場指数表を適用: {len(variants.table)} 日分")

    precomp = precompute(dataset, variants=variants)

    factors = active_factors(precomp)
    excluded = [f for f in FACTORS if f not in factors]
    if excluded:
        print(f"※ データに変化が無いため最適化から除外: {', '.join(excluded)}（重み 0 固定）")

    baseline = evaluate_weights(precomp, DEFAULT_WEIGHTS)
    print(
        f"\n現行デフォルト重み {DEFAULT_WEIGHTS}:"
        f" ◎勝率 {baseline['win_rate']:.1%} / ◎複勝率 {baseline['place_rate']:.1%}"
        f" / 単勝回収率 {baseline['roi']:.1f}%"
    )

    results = grid_search(precomp, step=args.step, objective=args.objective)

    print(f"\n=== 最適化結果（目的関数: {args.objective}、上位10件）===")
    print(
        f"{'speed':>6} {'workout':>8} {'pedigree':>9} {'◎勝率':>7} {'◎複勝率':>8}"
        f" {'単回収':>7} {'複回収':>7} {'印3頭中':>7}"
    )
    for r in results[:10]:
        w = r["weights"]
        print(
            f"{w['speed']:>6.2f} {w['workout']:>8.2f} {w['pedigree']:>9.2f}"
            f" {r['win_rate']:>7.1%} {r['place_rate']:>8.1%} {r['roi']:>6.1f}%"
            f" {r['place_roi']:>6.1f}% {r['top3_hit']:>7.2f}"
        )

    best = results[0]
    print(f"\n最適重み: {best['weights']}")
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(best["weights"], f, ensure_ascii=False, indent=2)
        print(f"{args.output} に保存しました（keiba predict --weights-file {args.output} で使用）")
    return 0


def cmd_fit(args: argparse.Namespace) -> int:
    """ロジスティック回帰で「3着以内」を予測し、係数から重みを学習する。

    グリッドサーチ(optimize)と違い、要素間の相対的な寄与を連続値で推定する。
    学習した重みは通常の weights.json として保存され、予想時に
    scikit-learn は不要（--weights-file で読むだけ）。
    """
    try:
        from sklearn.linear_model import LogisticRegression
    except ImportError:
        print("keiba fit には scikit-learn が必要です: pip install scikit-learn")
        return 1

    from .backtest import FACTORS, active_factors, evaluate_weights, precompute
    from .predictor import DEFAULT_WEIGHTS

    from .scrape.dataset import load_dataset

    dataset = load_dataset(args.dataset)

    variants = None
    if args.variants:
        from .track_variant import VariantTable

        variants = VariantTable.load(args.variants)

    precomp = precompute(dataset, variants=variants)
    factors = active_factors(precomp)
    print(f"データセット: {len(dataset['races'])} レース / 学習対象要素: {', '.join(factors)}")

    X, y = [], []
    for race in precomp:
        for i, dev in enumerate(race.deviations):
            if race.finish[i] is None:
                continue
            X.append([dev[f] for f in factors])
            y.append(1 if race.finish[i] <= 3 else 0)

    model = LogisticRegression(max_iter=1000)
    model.fit(X, y)

    raw = dict(zip(factors, model.coef_[0]))
    print("回帰係数(偏差値1ポイントあたりの3着内オッズ対数変化):")
    for f in factors:
        print(f"  {f:>10}: {raw[f]:+.4f}")

    # 負の係数は「効いていない/逆効果」なので重み0に落とし、正の係数を正規化
    positive = {f: max(0.0, float(c)) for f, c in raw.items()}
    total = sum(positive.values())
    if total <= 0:
        print("全要素の係数が非正のため重みを学習できませんでした")
        return 1
    weights = {f: round(positive.get(f, 0.0) / total, 3) for f in FACTORS}

    fitted = evaluate_weights(precomp, weights)
    baseline = evaluate_weights(precomp, DEFAULT_WEIGHTS)
    print(f"\n学習した重み: {weights}")
    print(
        f"  学習重み  : ◎勝率 {fitted['win_rate']:.1%} / ◎複勝率 {fitted['place_rate']:.1%}"
        f" / 単勝回収率 {fitted['roi']:.1f}% / 複勝回収率 {fitted['place_roi']:.1f}%"
    )
    print(
        f"  デフォルト: ◎勝率 {baseline['win_rate']:.1%} / ◎複勝率 {baseline['place_rate']:.1%}"
        f" / 単勝回収率 {baseline['roi']:.1f}% / 複勝回収率 {baseline['place_roi']:.1f}%"
    )

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(weights, f, ensure_ascii=False, indent=2)
        print(f"{args.output} に保存しました（keiba predict --weights-file {args.output} で使用）")
    return 0


def _result_rows_from_dataset(dataset: dict):
    """データセット JSON から走破タイムの行データを取り出す。"""
    for race_data in dataset["races"]:
        info = race_data["race"]
        for horse in race_data["horses"]:
            result = horse.get("result", {})
            if result.get("time_sec") and result.get("finish_position"):
                yield {
                    "race_id": info.get("race_id"),
                    "date": info.get("date", ""),
                    "course": info["course"],
                    "surface": info["surface"],
                    "distance": info["distance"],
                    "race_class": info["race_class"],
                    "going": info["going"],
                    "time_sec": result["time_sec"],
                }


def cmd_build_variants(args: argparse.Namespace) -> int:
    """データセットから同日レースの馬場指数表を算出する。"""
    from .scrape.dataset import load_dataset
    from .track_variant import VariantTable, compute_variants

    dataset = load_dataset(args.dataset)

    variants = compute_variants(
        _result_rows_from_dataset(dataset),
        min_races=args.min_races,
        clamp=args.clamp,
    )
    VariantTable(variants).save(args.output)
    print(f"{len(variants)} 日分の馬場指数を {args.output} に書き出しました")
    if variants:
        values = sorted(variants.values())
        print(f"  範囲: {values[0]:+.1f} 〜 {values[-1]:+.1f} / 中央値: {values[len(values) // 2]:+.1f}")
    print("※ keiba predict / optimize の --variants オプションで適用できます")
    return 0


def cmd_build_base_times(args: argparse.Namespace) -> int:
    """レース結果から基準タイム表を再構築する。

    入力は 2 形式:
    - CSV（ヘッダー: course,surface,distance,race_class,going,time_sec）
    - keiba scrape が出力したデータセット JSON

    良馬場のレースのみ集計し、クラス補正を戻して 1勝クラス相当に正規化した
    平均タイムを基準タイムとする。
    """
    from .speed_index import _load_base_times

    offsets = _load_base_times()["class_offsets"]
    buckets: dict[str, list[float]] = defaultdict(list)

    if args.results_file.endswith((".json", ".json.gz", ".gz")):
        from .scrape.dataset import load_dataset

        rows = _result_rows_from_dataset(load_dataset(args.results_file))
    else:
        f = open(args.results_file, encoding="utf-8", newline="")
        rows = csv.DictReader(f)

    for row in rows:
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
    print("※ src/keiba/data/base_times.json を置き換えると予想に反映されます")
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
    p_predict.add_argument("--weights-file", default=None, help="keiba optimize が出力した重み JSON")
    p_predict.add_argument(
        "--variants", default=None, help="keiba build-variants が出力した馬場指数表 JSON"
    )
    p_predict.add_argument("--json", action="store_true", help="JSON 形式で出力")
    p_predict.set_defaults(func=cmd_predict)

    p_index = sub.add_parser("speed-index", help="単発でスピード指数を計算")
    p_index.add_argument("--course", required=True, help="競馬場名（例: 東京)")
    p_index.add_argument("--surface", required=True, choices=["芝", "ダ"])
    p_index.add_argument("--distance", type=int, required=True)
    p_index.add_argument("--time", type=float, required=True, help="走破タイム（秒）")
    p_index.add_argument("--weight", type=float, default=56.0, help="斤量")
    p_index.add_argument("--going", default="良", choices=["良", "稍重", "重", "不良"])
    p_index.add_argument("--race-class", default="1勝", help="クラス（例: 2勝, OP, G1）")
    p_index.add_argument("--track-variant", type=float, default=None, help="馬場指数（実測値）")
    p_index.set_defaults(func=cmd_speed_index)

    p_scrape = sub.add_parser("scrape", help="netkeiba からバックテスト用データセットを構築")
    p_scrape.add_argument("--start", required=True, help="開始日 YYYY-MM-DD")
    p_scrape.add_argument("--end", required=True, help="終了日 YYYY-MM-DD")
    p_scrape.add_argument("-o", "--output", default="dataset.json")
    p_scrape.add_argument("--surface", choices=["芝", "ダ"], default=None, help="コース種別で絞り込み")
    p_scrape.add_argument("--max-races", type=int, default=None, help="取得レース数の上限")
    p_scrape.add_argument("--min-past-races", type=int, default=2, help="必要な過去走数の下限")
    p_scrape.add_argument(
        "--results-only",
        action="store_true",
        help="馬ページを取得せずレース結果(全馬のタイム・斤量・オッズ)のみ収集。大量収集向き",
    )
    p_scrape.add_argument("--wait", type=float, default=1.5, help="リクエスト間隔（秒）")
    p_scrape.add_argument("--cache-dir", default="data/cache", help="HTML キャッシュディレクトリ")
    p_scrape.set_defaults(func=cmd_scrape)

    p_jra = sub.add_parser("scrape-jra", help="JRA公式の成績ページからレース結果を取得")
    p_jra.add_argument(
        "sources",
        nargs="+",
        help="成績ページのURL または 保存済みHTMLファイルのパス（複数指定可）",
    )
    p_jra.add_argument("-o", "--output", default="jra_results.json")
    p_jra.add_argument("--wait", type=float, default=1.5, help="リクエスト間隔（秒）")
    p_jra.add_argument("--cache-dir", default="data/cache", help="HTML キャッシュディレクトリ")
    p_jra.set_defaults(func=cmd_scrape_jra)

    p_opt = sub.add_parser("optimize", help="データセットで重みをグリッドサーチ最適化")
    p_opt.add_argument("dataset", help="keiba scrape が出力したデータセット JSON")
    p_opt.add_argument(
        "--objective",
        choices=["place_rate", "win_rate", "roi", "place_roi", "top3_hit"],
        default="place_rate",
        help="最適化の目的関数（デフォルト: ◎複勝率）",
    )
    p_opt.add_argument("--step", type=float, default=0.05, help="グリッドの刻み幅")
    p_opt.add_argument(
        "--variants", default=None, help="keiba build-variants が出力した馬場指数表 JSON"
    )
    p_opt.add_argument("-o", "--output", default=None, help="最適重みの保存先 JSON")
    p_opt.set_defaults(func=cmd_optimize)

    p_fit = sub.add_parser("fit", help="ロジスティック回帰で重みを学習(要scikit-learn)")
    p_fit.add_argument("dataset", help="keiba scrape が出力したデータセット JSON")
    p_fit.add_argument(
        "--variants", default=None, help="keiba build-variants が出力した馬場指数表 JSON"
    )
    p_fit.add_argument("-o", "--output", default=None, help="学習した重みの保存先 JSON")
    p_fit.set_defaults(func=cmd_fit)

    p_var = sub.add_parser("build-variants", help="データセットから同日レースの馬場指数表を算出")
    p_var.add_argument("dataset", help="keiba scrape が出力したデータセット JSON")
    p_var.add_argument("-o", "--output", default="track_variants.json")
    p_var.add_argument("--min-races", type=int, default=2, help="1日として採用する最小レース数")
    p_var.add_argument("--clamp", type=float, default=40.0, help="馬場指数の上下限（指数ポイント）")
    p_var.set_defaults(func=cmd_build_variants)

    p_build = sub.add_parser("build-base-times", help="レース結果から基準タイム表を構築")
    p_build.add_argument(
        "results_file",
        help="CSV（course,surface,distance,race_class,going,time_sec）または scrape のデータセット JSON",
    )
    p_build.add_argument("-o", "--output", default="base_times.json")
    p_build.add_argument("--min-samples", type=int, default=5, help="採用する最小サンプル数")
    p_build.set_defaults(func=cmd_build_base_times)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
