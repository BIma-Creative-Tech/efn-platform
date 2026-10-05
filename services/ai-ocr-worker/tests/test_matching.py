from efn_ocr.matching import name_similarity, normalize_name


def test_normalize_name() -> None:
    assert normalize_name("  Kópi KAPAL-Api 165g! ") == "kopi kapal api 165g"
    assert normalize_name(None) == ""


def test_exact_and_reordered_names_score_high() -> None:
    assert name_similarity("Indomie Goreng", "indomie goreng") == 100.0
    assert name_similarity("Goreng Indomie", "INDOMIE GORENG") >= 95.0


def test_name_inside_noisy_ocr_text() -> None:
    ocr_text = "SUPER PROMO INDOMIE GORENG SPESIAL 85g Rp 3.500 8991002101"
    assert name_similarity("Indomie Goreng", ocr_text) >= 75.0


def test_small_ocr_typo_still_matches() -> None:
    assert name_similarity("Indomie Goreng", "INDOMI GORENG Rp 3.500") >= 75.0


def test_different_product_scores_low() -> None:
    assert name_similarity("Beras Ramos 5kg", "Minyak Goreng Bimoli 2L") < 75.0


def test_empty_or_too_short_names_score_zero() -> None:
    assert name_similarity("", "indomie goreng") == 0.0
    assert name_similarity("ab", "ab cd ef") == 0.0
    assert name_similarity("Indomie", "") == 0.0
    assert name_similarity("Indomie", None) == 0.0
