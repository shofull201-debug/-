"""脚質×コース形態の評価。

- 脚質は 逃げ / 先行 / 差し / 追込 の4分類。
  HorseEntry.running_style の明示指定を最優先し、無ければ過去走の
  4角通過順位（PastRace.position_4c）から推定する。どちらも無ければ不明。
- コースごとの脚質適性は data/course_style.json に持つ
  （小回りは前有利、直線の長いコースは差し・追込が届きやすい等）。
- 脚質不明の馬は中立の 50 点。
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources

from .models import HorseEntry

STYLES = ("逃げ", "先行", "差し", "追込")

# 4角通過順位の相対位置(0=先頭〜1=最後方)から脚質を推定する閾値
SENKO_THRESHOLD = 0.35
SASHI_THRESHOLD = 0.65


@lru_cache(maxsize=1)
def _load_course_style() -> dict:
    with resources.files("keiba.data").joinpath("course_style.json").open(
        encoding="utf-8"
    ) as f:
        return json.load(f)


def infer_style(horse: HorseEntry) -> tuple[str | None, str]:
    """馬の脚質と判定根拠（"明示指定" / "通過順位から推定" / "不明"）を返す。"""
    if horse.running_style in STYLES:
        return horse.running_style, "明示指定"

    ratios = []
    lead_count = 0
    n = 0
    for race in horse.past_races:
        if race.position_4c and race.field_size and race.field_size > 1:
            n += 1
            ratios.append((race.position_4c - 1) / (race.field_size - 1))
            if race.position_4c == 1:
                lead_count += 1
    if not ratios:
        return None, "不明"

    # 過半数のレースで4角先頭なら逃げ
    if lead_count * 2 >= n:
        return "逃げ", "通過順位から推定"
    avg = sum(ratios) / len(ratios)
    if avg <= SENKO_THRESHOLD:
        return "先行", "通過順位から推定"
    if avg <= SASHI_THRESHOLD:
        return "差し", "通過順位から推定"
    return "追込", "通過順位から推定"


def course_style_aptitude(course: str, surface: str, distance: int) -> dict[str, int]:
    """コース形態ごとの脚質適性表（脚質→0〜10）を返す。"""
    data = _load_course_style()
    return (
        data["overrides"].get(f"{course}|{surface}|{distance}")
        or data["courses"].get(f"{course}|{surface}")
        or data["default"]
    )


def style_fit_score(
    horse: HorseEntry, course: str, surface: str, distance: int
) -> dict:
    """今回コースへの脚質適合度（0〜100）と内訳を返す。脚質不明は中立50。"""
    style, source = infer_style(horse)
    if style is None:
        return {"score": 50.0, "style": None, "source": source}
    aptitude = course_style_aptitude(course, surface, distance)
    return {
        "score": aptitude.get(style, 5) * 10.0,
        "style": style,
        "source": source,
    }
