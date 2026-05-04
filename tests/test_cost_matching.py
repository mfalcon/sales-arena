"""Tests for cost/product fuzzy matching — BUG-005."""

from arena.evaluation import _find_cost


COST_MAP = {
    "iPhone 15": 799,
    "iPhone 15 Pro Max": 1099,
    "Samsung Galaxy S24": 699,
    "Samsung Galaxy S24 Ultra": 1049,
    "Google Pixel 8": 549,
    "Google Pixel 8 Pro": 749,
    "Xiaomi 14": 399,
    "Motorola Edge 40 Pro": 449,
    "OnePlus 12": 599,
    "Samsung Galaxy A55": 249,
}


def test_exact_match():
    assert _find_cost("iPhone 15", COST_MAP) == 799
    assert _find_cost("Samsung Galaxy S24 Ultra", COST_MAP) == 1049


def test_case_insensitive():
    assert _find_cost("iphone 15", COST_MAP) == 799
    assert _find_cost("SAMSUNG GALAXY A55", COST_MAP) == 249


def test_s24_ultra_not_s24():
    """BUG-005: 'Samsung Galaxy S24 Ultra 256GB' should match S24 Ultra ($1049), not S24 ($699)."""
    assert _find_cost("Samsung Galaxy S24 Ultra 256GB", COST_MAP) == 1049


def test_s24_plain():
    """Plain S24 should match S24, not S24 Ultra."""
    assert _find_cost("Samsung Galaxy S24", COST_MAP) == 699


def test_pixel8_pro_not_pixel8():
    """'Google Pixel 8 Pro 128GB' should match Pixel 8 Pro ($749), not Pixel 8 ($549)."""
    assert _find_cost("Google Pixel 8 Pro 128GB", COST_MAP) == 749


def test_pixel8_plain():
    assert _find_cost("Google Pixel 8", COST_MAP) == 549


def test_iphone15_pro_max_not_iphone15():
    """'iPhone 15 Pro Max (256GB)' should match Pro Max ($1099), not iPhone 15 ($799)."""
    assert _find_cost("iPhone 15 Pro Max (256GB)", COST_MAP) == 1099


def test_iphone15_plain():
    assert _find_cost("iPhone 15", COST_MAP) == 799


def test_product_with_color():
    """Consumer adds color — should still match."""
    assert _find_cost("Xiaomi 14 en negro", COST_MAP) == 399


def test_unknown_product():
    assert _find_cost("Nokia 3310", COST_MAP) == 0.0


def test_empty_product():
    assert _find_cost("", COST_MAP) == 0.0
