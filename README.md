# 競馬予想システム

血統・追切・過去5走の西田式スピード指数の3要素で出走馬を総合評価する予想システムです。
予想エンジン本体は Python 標準ライブラリのみで動作します
（netkeiba からのデータ取得のみ beautifulsoup4 が必要）。

## 評価の仕組み

```
┌─────────────────────┐
│ 過去5走             │──▶ 西田式スピード指数 ──┐
├─────────────────────┤                          │  メンバー内偏差値に変換し
│ 血統（父・母父）    │──▶ コース×距離適性   ──┼─▶ 重み付き合成 ──▶ 総合点・印
├─────────────────────┤                          │  （デフォルト 速5:調3:血2）
│ 追切                │──▶ 時計・脚色・併せ  ──┘
└─────────────────────┘
```

### 1. 西田式スピード指数（`speed_index.py`）

```
指数 = (基準タイム − 走破タイム) × 距離指数 × 10 + 馬場指数 + (斤量 − 55) × 2 + 80
```

- **基準タイム**: 競馬場×コース種別×距離ごとの 1勝クラス標準タイム（`data/base_times.json`）。
  クラス差は距離に比例した補正で吸収します。
- **距離指数**: 距離ごとの「1秒の価値」を揃える係数。表に無い距離は線形補間。
- **馬場指数**: その日の馬場差。実測値があれば `track_variant` で指定、
  無ければ馬場状態（良/稍重/重/不良）から概算します（芝は渋るとプラス、ダートはマイナス）。
- **過去5走の集約**: 鮮度（直近ほど重い）×今回条件への関連度（芝⇔ダート替わり・距離差で減衰）
  の加重平均と、ベスト指数をブレンドします。

### 2. 血統評価（`pedigree.py`）

父 65% + 母父 35% の比率で、種牡馬ごとの「コース適性 × 距離カテゴリ適性」
（`data/sire_aptitude.json`、0〜10）から今回条件への適合度を 0〜100 点で採点します。
未登録の種牡馬は中立の 50 点。**種牡馬データは JSON を編集して自由に追加・調整できます。**

### 3. 追切評価（`workout.py`）

トレセン×コース（栗東坂路/美浦W など）ごとの基準タイム表（`data/workout_standards.json`）と
比較して全体時計と終い1Fを採点し、追い方（馬なりで好時計は加点、一杯は減点）と
併せ馬の結果（先着/同入/遅れ）を加味します。最終追い切りを 70% の重みで評価します。

### 4. 総合評価（`predictor.py`）

3要素はスケールが異なるため、出走メンバー内の**偏差値**に変換してから
重み（デフォルト: スピード 0.5 / 追切 0.3 / 血統 0.2）で合成し、
上位から ◎○▲△△ の印を付けます。

## 使い方

```bash
pip install -e .

# レースカード JSON から予想
keiba predict data/sample_race.json
keiba predict race.json --weights speed=0.6,workout=0.25,pedigree=0.15
keiba predict race.json --json   # JSON 出力

# 単発でスピード指数を計算
keiba speed-index --course 東京 --surface 芝 --distance 1600 \
  --time 92.8 --weight 57 --race-class OP

# 手持ちのレース結果 CSV から基準タイム表を再構築
keiba build-base-times results.csv -o base_times.json
```

出力例:

```
=== サンプルステークス ===
東京 芝1600m OP 馬場:良

印   馬番 馬名               総合    速偏    調偏    血偏  過去5走指数(直近→)
◎    5 フレッシュスター       61.7  63.1  61.8  57.8  85 88
○    1 サンプルウイナー       58.1  54.1  60.6  64.4  87 82 75 82 76
▲    4 ダートモンスター       52.2  60.3  47.1  39.8  88 87 82 79 85
...
```

## 入力データ形式

レースカードは JSON で記述します（完全な例は `data/sample_race.json`）:

```jsonc
{
  "race": {
    "name": "サンプルステークス",
    "course": "東京", "surface": "芝", "distance": 1600,
    "going": "良", "race_class": "OP", "date": "2026-07-19"
  },
  "horses": [
    {
      "name": "サンプルウイナー",
      "horse_number": 1,
      "sire": "ディープインパクト",      // 父
      "dam_sire": "キングカメハメハ",     // 母父
      "weight_carried": 57.0,             // 今回斤量
      "past_races": [                     // 過去5走（新しい順でなくても可）
        {
          "date": "2026-06-07", "course": "東京", "surface": "芝",
          "distance": 1600, "going": "良",
          "time_sec": 92.8,               // 1:32.8 → 92.8
          "weight_carried": 57.0,
          "finish_position": 2, "field_size": 14,
          "race_class": "OP",
          "track_variant": null           // 馬場指数の実測値（任意）
        }
      ],
      "workouts": [                       // 追切（新しい順でなくても可）
        {
          "date": "2026-07-15", "facility": "美浦", "course": "W",
          "furlongs": 6, "total_time": 80.5, "last_1f": 11.9,
          "intensity": "馬なり",          // 一杯/強め/馬なり/G前仕掛け
          "partner_result": "先着"        // 先着/同入/遅れ、単走なら省略
        }
      ]
    }
  ]
}
```

## Python API

```python
import json
from keiba import RaceCard, predict, nishida_speed_index

with open("data/sample_race.json", encoding="utf-8") as f:
    card = RaceCard.from_dict(json.load(f))

for r in predict(card):
    print(r.mark, r.name, r.total, r.speed_indices)
```

## netkeiba からのデータ取得と重みの最適化

実データで基準タイムの再構築と重みの最適化ができます。

```bash
pip install -e ".[scrape]"   # beautifulsoup4 を追加インストール

# 1. netkeiba (db.netkeiba.com) から期間内のレース結果・出走馬の過去走・血統を収集
keiba scrape --start 2026-04-01 --end 2026-06-30 -o dataset.json
#   --surface 芝            芝レースのみに絞る
#   --max-races 200         レース数の上限
#   --wait 1.5              リクエスト間隔（秒）。短くしすぎないこと

# 2. 収集した結果から基準タイム表を再構築（同梱の目安値を実データで置き換え）
keiba build-base-times dataset.json -o base_times.json
cp base_times.json src/keiba/data/base_times.json

# 3. 同日レース結果から馬場指数表を算出（基準タイム差し替え後に実行すること）
keiba build-variants dataset.json -o track_variants.json

# 4. 重みをグリッドサーチで最適化（◎の複勝率/勝率/回収率でバックテスト）
keiba optimize dataset.json --variants track_variants.json -o weights.json

# 5. 最適化された重み + 馬場指数で予想
keiba predict race.json --weights-file weights.json --variants track_variants.json
```

`keiba optimize` の出力例:

```
データセット: 20 レース
※ データに変化が無いため最適化から除外: workout（重み 0 固定）

現行デフォルト重み {'speed': 0.5, 'workout': 0.3, 'pedigree': 0.2}: ◎勝率 50.0% / ◎複勝率 100.0% / 単勝回収率 250.0%

=== 最適化結果（目的関数: place_rate、上位10件）===
 speed  workout  pedigree     ◎勝率    ◎複勝率    回収率   印3頭中
  0.90     0.00      0.10  100.0%   100.0%  500.0%    3.00
  ...
最適重み: {'speed': 0.9, 'workout': 0.0, 'pedigree': 0.1}
```

### 馬場指数の自動算出（build-variants）

同日・同競馬場・同コース種別（芝/ダ）の全レースについて
「走破タイムと基準タイムの乖離」を指数ポイントに換算し、
**レースごとの平均 → 日単位の中央値**（外れ値のハイレベル戦に強い）で
その日の馬場の速さを推定します。

- 時計のかかる馬場 → プラス、高速馬場 → マイナスの補正値
- 過去走の `track_variant` が未設定の走にだけ適用されます
  （手入力の実測値があればそちらが優先）
- 適用された走は、馬場状態（良/稍重/重/不良）からの概算補正の代わりに
  この実測ベースの補正でスピード指数が計算されます
- `--min-races`（デフォルト2）未満しかレースが無い日は信頼性が低いためスキップ
- **基準タイムを再構築した場合は馬場指数も算出し直してください**（乖離の基準が変わるため）

### スクレイピングに関する注意

- **netkeiba の利用規約を確認のうえ、個人利用の範囲で自己責任で使用してください。**
- デフォルトで 1.5 秒/リクエストのウェイトが入り、取得済みページは
  `data/cache/` にキャッシュされて再取得しません（中断しても再開が速い）。
- 馬ページは 1 頭 1 回しか取得しないため、リクエスト数の目安は
  「開催日数 + レース数 + ユニーク出走頭数」です。
  1 開催週の取得でも数百リクエスト（20〜30 分程度）かかります。
- 追切（調教）データは netkeiba ではプレミアム会員向けのため取得対象外です。
  そのためスクレイピングデータでの最適化では workout の重みは 0 に固定され、
  スピード指数と血統の比率が最適化されます。追切は予想時に手入力で活用してください。

### バックテストの指標

| 指標 | 意味 |
|---|---|
| `win_rate` | ◎（総合1位）が1着になった率 |
| `place_rate` | ◎が3着以内に入った率（デフォルトの目的関数） |
| `roi` | ◎に単勝100円を賭け続けた場合の回収率（%） |
| `top3_hit` | 印上位3頭のうち3着以内に入った頭数の平均 |

過去走データはレース当日より前の走歴だけを使う（リーク防止）よう構築されます。

## データのカスタマイズ

同梱の基準値は**あくまで目安**です。精度を上げるには自分のデータで調整してください。

| ファイル | 内容 | 調整方法 |
|---|---|---|
| `src/keiba/data/base_times.json` | 基準タイム・クラス補正 | `keiba scrape` + `keiba build-base-times` で実データから再構築 |
| `src/keiba/data/sire_aptitude.json` | 種牡馬の芝ダ・距離適性 | 産駒成績を見て 0〜10 で追記 |
| `src/keiba/data/workout_standards.json` | 追切の水準タイム | トレセン・コースごとに追記 |

## テスト

```bash
pip install pytest
python -m pytest tests/ -v
```

## 今後の拡張候補

- 枠順・脚質・展開の評価要素追加
- 種牡馬適性の実データからの自動算出（産駒成績の集計）
- グリッドサーチをロジスティック回帰等の学習ベース最適化に置き換え
