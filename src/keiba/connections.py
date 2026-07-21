"""騎手・調教師(厩舎)の評価。

過去成績の複勝率(3着内率)をベイズ縮小して 0-100 のスコアにする。
統計は scripts/build_connections.py がデータセットから構築し、
src/keiba/data/connections.json に保存される。

2022-2024で統計構築→2025-2026で追試した検証で、重み0.1の追加により
◎勝率 +1.9pt / ◎複勝率 +3.1pt の改善を確認済み(枠順・馬体重増減・
展開は同じ検証で効果なしだったため不採用)。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA_PATH = Path(__file__).parent / "data" / "connections.json"

SHRINK = 50          # ベイズ縮小: 平均的成績の仮想騎乗(管理)数
BASE_PLACE = 0.25    # 複勝率の事前値(全体平均)
JOCKEY_WEIGHT = 0.6  # 騎手と調教師のブレンド比率


@lru_cache(maxsize=1)
def _load_default() -> dict:
    if DATA_PATH.exists():
        return json.loads(DATA_PATH.read_text(encoding="utf-8"))
    return {"jockeys": {}, "trainers": {}}


def _rate(entry: list | None) -> float | None:
    if not entry:
        return None
    places, rides = entry
    return (places + BASE_PLACE * SHRINK) / (rides + SHRINK)


def connections_score(
    jockey: str | None, trainer: str | None, data: dict | None = None
) -> dict:
    """騎手・調教師スコア(0-100)と内訳を返す。

    known=False は両者とも統計に無い(またはカード未記載)ことを示し、
    予想側は欠損として扱ってよい。
    """
    data = data if data is not None else _load_default()
    jr = _rate(data["jockeys"].get(jockey or ""))
    tr = _rate(data["trainers"].get(trainer or ""))
    known = jr is not None or tr is not None
    j = jr if jr is not None else BASE_PLACE
    t = tr if tr is not None else BASE_PLACE
    score = (j * JOCKEY_WEIGHT + t * (1 - JOCKEY_WEIGHT)) * 100
    return {
        "score": round(score, 1),
        "jockey_rate": round(jr, 3) if jr is not None else None,
        "trainer_rate": round(tr, 3) if tr is not None else None,
        "known": known,
    }
