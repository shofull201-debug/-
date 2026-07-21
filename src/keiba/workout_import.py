"""追い切り記事テキストからの取り込み。

うましる・血統フェスティバル等の追い切り記事(コピーしたテキストや
保存したHTML)から、カード出走馬の追切データを抽出する。

抽出するもの:
- 施設(栗東/美浦/函館/札幌…) とコース(坂路/CW/W/P/芝/ダ)
- ハロン毎の時計列(例 52.3-38.1-24.6-12.1 → 4F通し52.3・終い12.1)
- 脚色(馬なり/強め/一杯/G前仕掛け)
- 併せ馬の結果(先着/同入/遅れ)
- 日付(「6月25日」「6/25」を年補完)

馬名ごとのブロック分割はカードの馬名の出現位置で行うため、
記事に載っていない馬は単にスキップされる。
"""

from __future__ import annotations

import re
import unicodedata

from .models import RaceCard

FACILITIES = ("栗東", "美浦", "函館", "札幌", "小倉", "福島", "新潟", "中京")
COURSE_MAP = {
    "坂路": "坂路", "CW": "W", "ウッド": "W", "W": "W",
    "P": "P", "ポリ": "P", "芝": "芝", "ダート": "ダ", "ダ": "ダ",
}
INTENSITIES = ("G前仕掛け", "一杯", "強め", "馬なり", "直線一杯", "仕掛け")
INTENSITY_MAP = {"直線一杯": "一杯", "仕掛け": "G前仕掛け"}

# 52.3-38.1-24.6-12.1 のような時計列(2〜6分割)
TIME_SEQ_FULL = re.compile(r"(?<![\d.])\d{2,3}\.\d(?:\s*[-−ー]\s*\d{2}\.\d){1,5}")
DATE_PAT = re.compile(r"(\d{1,2})[月/](\d{1,2})日?")


def strip_html(text: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text,
                  flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return unicodedata.normalize("NFKC", text)


def _find_date(chunk: str, year: int) -> str | None:
    m = DATE_PAT.search(chunk)
    if not m:
        return None
    return f"{year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"


def parse_workout_block(chunk: str, year: int) -> dict | None:
    """馬名ブロック1つ分のテキストから追切1本を抽出する。"""
    m = TIME_SEQ_FULL.search(chunk)
    if not m:
        return None
    times = [float(t) for t in re.split(r"\s*[-−ー]\s*", m.group(0))]
    if any(t2 >= t1 for t1, t2 in zip(times, times[1:])):
        return None  # 時計列は単調減少のはず(ラップでなければ捨てる)

    window = chunk[max(0, m.start() - 80): m.end() + 80]
    facility = next((f for f in FACILITIES if f in window), None)
    course = None
    for key, val in COURSE_MAP.items():
        if key in window:
            course = val
            break
    if course is None:
        # 4F通しで55秒前後なら坂路とみなす
        course = "坂路" if times[0] < 60 and len(times) <= 4 else "W"
    intensity = "馬なり"
    for word in INTENSITIES:
        if word in window:
            intensity = INTENSITY_MAP.get(word, word)
            break
    partner = None
    if re.search(r"(先着|遅れ|同入)", window):
        partner = re.search(r"(先着|遅れ|同入)", window).group(1)

    # 通し時計から計測ハロン数を推定(1F≒12〜13秒 + 助走)
    furlongs = len(times) if len(times) >= 4 else (
        4 if times[0] < 60 else 5 if times[0] < 73 else 6
    )
    return {
        "date": _find_date(chunk, year) or f"{year}-01-01",
        "facility": facility or "その他",
        "course": course,
        "furlongs": furlongs,
        "total_time": times[0],
        "last_1f": times[-1],
        "intensity": intensity,
        "partner_result": partner,
    }


def extract_workouts(text: str, horse_names: list[str], year: int) -> dict[str, dict]:
    """記事テキストから馬名→追切データを抽出する。"""
    if "<" in text and ">" in text:
        text = strip_html(text)
    else:
        text = unicodedata.normalize("NFKC", text)

    # 各馬名の最初の出現位置でブロック分割
    positions = []
    for name in horse_names:
        pos = text.find(name)
        if pos >= 0:
            positions.append((pos, name))
    positions.sort()

    found = {}
    for i, (pos, name) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        block = parse_workout_block(text[pos:end], year)
        if block:
            found[name] = block
    return found


def merge_into_card(card_data: dict, workouts: dict[str, dict],
                    replace: bool = False) -> int:
    """抽出した追切をカードdictへ反映する。既存の追切は残す(replace=Trueで置換)。"""
    applied = 0
    for horse in card_data.get("horses", []):
        w = workouts.get(horse["name"])
        if w is None:
            continue
        existing = horse.get("workouts") or []
        if existing and not replace:
            continue
        horse["workouts"] = [w]
        applied += 1
    return applied


def card_horse_names(card_data: dict) -> list[str]:
    return [h["name"] for h in card_data.get("horses", [])]


__all__ = [
    "extract_workouts", "merge_into_card", "parse_workout_block",
    "card_horse_names", "strip_html", "RaceCard",
]
