from keiba.pedigree import distance_category, pedigree_score


class TestDistanceCategory:
    def test_boundaries(self):
        assert distance_category(1200) == "短距離"
        assert distance_category(1400) == "短距離"
        assert distance_category(1401) == "マイル"
        assert distance_category(1800) == "マイル"
        assert distance_category(2000) == "中距離"
        assert distance_category(2400) == "中距離"
        assert distance_category(3000) == "長距離"


class TestPedigreeScore:
    def test_turf_miler_sire_on_turf_mile(self):
        result = pedigree_score("ディープインパクト", None, "芝", 1600)
        assert result["sire_known"] is True
        assert result["score"] >= 90  # 芝10 × マイル9 → (10*0.5 + 9*0.5) * 10 = 95

    def test_dirt_sire_penalized_on_turf(self):
        on_dirt = pedigree_score("ヘニーヒューズ", None, "ダ", 1400)
        on_turf = pedigree_score("ヘニーヒューズ", None, "芝", 1400)
        assert on_dirt["score"] > on_turf["score"]

    def test_sprinter_sire_penalized_at_long_distance(self):
        sprint = pedigree_score("ロードカナロア", None, "芝", 1200)
        long = pedigree_score("ロードカナロア", None, "芝", 3000)
        assert sprint["score"] > long["score"]

    def test_unknown_sire_gets_neutral_score(self):
        result = pedigree_score("無名種牡馬", None, "芝", 1600)
        assert result["sire_known"] is False
        assert result["score"] == 50.0

    def test_dam_sire_blended(self):
        without = pedigree_score("ヘニーヒューズ", None, "芝", 1600)
        with_ds = pedigree_score("ヘニーヒューズ", "ディープインパクト", "芝", 1600)
        # 芝適性の高い母父が加わるとスコアが上がる
        assert with_ds["score"] > without["score"]
        assert with_ds["dam_sire_score"] is not None
