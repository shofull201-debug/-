"""騎手・調教師評価のテスト。"""

from keiba.connections import BASE_PLACE, connections_score

DATA = {
    "jockeys": {"上手騎手": [40, 100], "下手騎手": [10, 100]},
    "trainers": {"名門厩舎": [35, 100]},
}


class TestConnectionsScore:
    def test_good_jockey_scores_higher(self):
        good = connections_score("上手騎手", None, DATA)
        bad = connections_score("下手騎手", None, DATA)
        assert good["score"] > bad["score"]
        assert good["known"] and bad["known"]

    def test_unknown_both_is_not_known(self):
        r = connections_score("無名", "無名", DATA)
        assert not r["known"]
        # スコアは中立(事前値)相当
        assert abs(r["score"] - BASE_PLACE * 100) < 1e-6

    def test_trainer_only_still_known(self):
        r = connections_score(None, "名門厩舎", DATA)
        assert r["known"]
        assert r["trainer_rate"] is not None
        assert r["jockey_rate"] is None

    def test_shrinkage_pulls_small_sample_to_base(self):
        # 少数騎乗の好成績は事前値へ寄る
        data = {"jockeys": {"新人": [5, 10]}, "trainers": {}}
        r = connections_score("新人", None, data)
        raw_rate = 5 / 10
        assert BASE_PLACE * 100 < r["score"] < raw_rate * 100 * 0.6 + BASE_PLACE * 100 * 0.4


class TestPredictorIntegration:
    def test_card_without_jockey_treated_as_missing(self):
        import json
        from pathlib import Path

        from keiba.models import RaceCard
        from keiba.predictor import predict

        sample = Path(__file__).parent.parent / "data" / "sample_race.json"
        card = RaceCard.from_dict(json.loads(sample.read_text(encoding="utf-8")))
        results = predict(card)
        # サンプルカードに騎手情報は無い → 騎偏は欠損(None)で総合は計算される
        for r in results:
            assert r.deviations["connections"] is None
