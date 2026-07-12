# 競馬予想システム

血統・追切・過去5走の西田式スピード指数の3要素で出走馬を総合評価する予想システムです。
Python 標準ライブラリのみで動作します（外部依存なし）。

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

## データのカスタマイズ

同梱の基準値は**あくまで目安**です。精度を上げるには自分のデータで調整してください。

| ファイル | 内容 | 調整方法 |
|---|---|---|
| `src/keiba/data/base_times.json` | 基準タイム・クラス補正 | `keiba build-base-times` で実データから再構築 |
| `src/keiba/data/sire_aptitude.json` | 種牡馬の芝ダ・距離適性 | 産駒成績を見て 0〜10 で追記 |
| `src/keiba/data/workout_standards.json` | 追切の水準タイム | トレセン・コースごとに追記 |

## テスト

```bash
pip install pytest
python -m pytest tests/ -v
```

## 今後の拡張候補

- 馬場指数の自動算出（同日レース結果からの逆算）
- 枠順・脚質・展開の評価要素追加
- netkeiba 等からのデータ取り込みスクリプト
- 過去レースでのバックテスト（回収率検証）と重みの最適化
