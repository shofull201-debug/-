"""JRA 公式サイト（jra.go.jp）のレース成績ページの取得とパース。

対象: https://www.jra.go.jp/datafile/seiseki/... のような成績ページ
（例: /datafile/seiseki/g1/arima/result/arima2025.html）

注意:
- JRA サイトの利用規約を確認し、個人利用の範囲で節度あるアクセスを守ること
  （NetkeibaClient のレート制限・キャッシュをそのまま利用する）。
- JRA のページは Shift_JIS エンコーディングで、数字・記号に全角文字が
  混在するため、NFKC 正規化してからパースする。
- 距離は「2,500メートル」のようにカンマ入りで表記される。
"""

from __future__ import annotations

import re
import unicodedata

from .netkeiba import (
    ParsedRace,
    RaceResultRow,
    _require_bs4,
    _to_float,
    _to_int,
    detect_class,
    parse_time,
)

COURSES = ("札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉")


def _normalize(text: str) -> str:
    """全角英数字・記号を半角へ正規化する（ＧⅠ → GI など）。"""
    return unicodedata.normalize("NFKC", text)


def parse_jra_result_page(html: str, url: str = "") -> ParsedRace | None:
    """JRA 成績ページをパースする。成績表が見つからなければ None。"""
    BeautifulSoup = _require_bs4()
    soup = BeautifulSoup(html, "html.parser")
    page_text = _normalize(soup.get_text(" ", strip=True))

    # ---- レース基本情報（ページ全文からの正規表現抽出） ----
    md = re.search(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", page_text)
    date = f"{md.group(1)}-{int(md.group(2)):02d}-{int(md.group(3)):02d}" if md else ""

    mc = re.search(r"\d+回(" + "|".join(COURSES) + r")\d+日", page_text)
    course = mc.group(1) if mc else ""

    ms = re.search(r"(芝|ダート)[^0-9]{0,20}?([0-9,，]{3,6})\s*メートル", page_text)
    if not ms:
        return None
    surface = "芝" if ms.group(1) == "芝" else "ダ"
    distance = int(ms.group(2).replace(",", "").replace("，", ""))

    mg = re.search(r"馬場(?:状態)?\s*[：:]?\s*(良|稍重|重|不良)", page_text)
    if not mg:
        mg = re.search(r"(?:芝|ダート)\s*[：:]\s*(良|稍重|重|不良)", page_text)
    going = mg.group(1) if mg else "良"

    title = soup.find("title")
    h1 = soup.find(["h1", "h2"])
    name_text = _normalize(
        (h1.get_text(" ", strip=True) if h1 else "")
        + " "
        + (title.get_text(" ", strip=True) if title else "")
    )
    # 「第70回有馬記念(GI)」形式を優先し、無ければ「2025年 有馬記念 JRA」形式から抽出
    mn = re.search(r"第\d+回\s*([^\s(（)]+)", name_text)
    if mn:
        name = mn.group(1)
    else:
        cleaned = re.sub(r"\d{4}年", " ", name_text).replace("JRA", " ")
        tokens = cleaned.split()
        name = tokens[0] if tokens else ""
    race_class = detect_class(name_text + " " + page_text[:500])

    # ---- 成績表: ヘッダーに「着順」と「馬名」を含むテーブルを探す ----
    result_table = None
    headers: list[str] = []
    for table in soup.find_all("table"):
        first_row = table.find("tr")
        if first_row is None:
            continue
        cells = [
            _normalize(c.get_text(strip=True))
            for c in first_row.find_all(["th", "td"])
        ]
        if any("着順" in c for c in cells) and any("馬名" in c for c in cells):
            result_table = table
            headers = cells
            break
    if result_table is None:
        return None

    def col(label: str) -> int | None:
        for i, h in enumerate(headers):
            if label in h:
                return i
        return None

    idx = {
        "finish": col("着順"),
        "num": col("馬番"),
        "name": col("馬名"),
        "weight": col("負担重量") if col("負担重量") is not None else col("斤量"),
        "time": col("タイム"),
        "pop": col("人気"),
    }

    rows: list[RaceResultRow] = []
    for tr in result_table.find_all("tr")[1:]:
        tds = tr.find_all(["td", "th"])
        needed = [i for i in idx.values() if i is not None]
        if not needed or len(tds) <= max(needed):
            continue

        def cell(key: str) -> str:
            i = idx[key]
            return _normalize(tds[i].get_text(strip=True)) if i is not None else ""

        name_cell = cell("name")
        if not name_cell:
            continue
        rows.append(
            RaceResultRow(
                finish_position=_to_int(cell("finish")),
                horse_number=_to_int(cell("num")) or 0,
                name=name_cell,
                horse_id="",
                weight_carried=_to_float(cell("weight")) or 56.0,
                time_sec=parse_time(cell("time")),
                odds=None,  # JRA成績ページには単勝オッズ列が無い（人気のみ）
                popularity=_to_int(cell("pop")),
            )
        )

    if not rows:
        return None

    return ParsedRace(
        race_id=url,
        name=name,
        date=date,
        course=course,
        surface=surface,
        distance=distance,
        going=going,
        race_class=race_class,
        rows=rows,
    )


def to_dataset_race(parsed: ParsedRace) -> dict:
    """パース結果を keiba のデータセット形式のレースエントリに変換する。

    出走馬の past_races は含まれない（結果のみ）。
    build-base-times / build-variants の入力として使える。
    """
    return {
        "race": {
            "race_id": parsed.race_id,
            "name": parsed.name,
            "date": parsed.date,
            "course": parsed.course,
            "surface": parsed.surface,
            "distance": parsed.distance,
            "going": parsed.going,
            "race_class": parsed.race_class,
        },
        "horses": [
            {
                "name": row.name,
                "horse_number": row.horse_number,
                "sire": "",
                "weight_carried": row.weight_carried,
                "past_races": [],
                "workouts": [],
                "result": {
                    "finish_position": row.finish_position,
                    "time_sec": row.time_sec,
                    "odds": row.odds,
                    "popularity": row.popularity,
                },
            }
            for row in parsed.rows
        ],
    }
