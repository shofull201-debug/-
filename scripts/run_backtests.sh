#!/usr/bin/env bash
# 定期バックテスト一括実行。reports/YYYY-MM-DD/ に結果を保存する。
#
# 使い方:  bash scripts/run_backtests.sh [出力先ディレクトリ]
set -uo pipefail
cd "$(dirname "$0")/.."

DATASET=data/dataset_2022_2026_v3.json.gz
VARIANTS=data/track_variants_2022_2026.json
OUT=${1:-reports/$(date +%F)}
mkdir -p "$OUT"

echo "== テスト =="                | tee "$OUT/summary.txt"
python -m pytest tests/ -q 2>&1 | tail -1 | tee -a "$OUT/summary.txt"

echo "== 重賞バックテスト(全期間) ==" | tee -a "$OUT/summary.txt"
python scripts/backtest_graded.py "$DATASET" --variants "$VARIANTS" \
    --min-date 2022-07-01 --report "$OUT/graded.md" | tee -a "$OUT/summary.txt"

echo "== 学習/検証分離の重み確認 ==" | tee -a "$OUT/summary.txt"
python scripts/tune_agari.py "$DATASET" --variants "$VARIANTS" \
    | tee "$OUT/agari.txt" | tail -8 | tee -a "$OUT/summary.txt"

echo "== 追加要素の効果確認 =="     | tee -a "$OUT/summary.txt"
python scripts/tune_extras.py "$DATASET" --variants "$VARIANTS" \
    | tee "$OUT/extras.txt" | tail -12 | tee -a "$OUT/summary.txt"

echo "== 指数×オッズ帯マトリクス ==" | tee -a "$OUT/summary.txt"
python scripts/value_analysis.py "$DATASET" --variants "$VARIANTS" \
    > "$OUT/value_matrix.txt"
tail -4 "$OUT/value_matrix.txt" | tee -a "$OUT/summary.txt"

echo "完了: $OUT"
