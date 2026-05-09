"""Tests for StockTracker."""

from arena.stock import StockTracker


def test_initial_stock():
    st = StockTracker({"iPhone 15": 5, "Galaxy A55": 12})
    assert st.get_stock("iPhone 15") == 5
    assert st.get_stock("Galaxy A55") == 12


def test_sell_decrements():
    st = StockTracker({"iPhone 15": 3})
    assert st.sell("iPhone 15") is True
    assert st.get_stock("iPhone 15") == 2
    assert st.sell("iPhone 15") is True
    assert st.get_stock("iPhone 15") == 1


def test_sell_out_of_stock():
    st = StockTracker({"iPhone 15": 1})
    assert st.sell("iPhone 15") is True
    assert st.get_stock("iPhone 15") == 0
    assert st.sell("iPhone 15") is False  # can't sell, stock is 0
    assert st.get_stock("iPhone 15") == 0  # stock stays 0


def test_sell_unknown_product():
    st = StockTracker({"iPhone 15": 5})
    assert st.sell("Nokia 3310") is False


def test_fuzzy_match_case_insensitive():
    st = StockTracker({"iPhone 15 Pro Max": 3})
    assert st.sell("iphone 15 pro max") is True
    assert st.get_stock("iPhone 15 Pro Max") == 2


def test_fuzzy_match_substring():
    st = StockTracker({"Samsung Galaxy S24 Ultra": 4})
    # Stock key contained in product string
    assert st.sell("Samsung Galaxy S24 Ultra en negro") is True
    assert st.get_stock("Samsung Galaxy S24 Ultra") == 3


def test_get_stock_text():
    st = StockTracker({"iPhone 15": 2, "Galaxy A55": 0})
    text = st.get_stock_text()
    assert "iPhone 15: 2 available" in text
    assert "Galaxy A55: OUT OF STOCK" in text


def test_snapshot_is_copy():
    st = StockTracker({"iPhone 15": 5})
    snap = st.snapshot()
    snap["iPhone 15"] = 999  # mutate the copy
    assert st.get_stock("iPhone 15") == 5  # original unchanged


def test_sell_does_not_go_negative():
    st = StockTracker({"iPhone 15": 0})
    assert st.sell("iPhone 15") is False
    assert st.get_stock("iPhone 15") == 0


def test_multiple_products_independent():
    st = StockTracker({"iPhone 15": 2, "Pixel 8": 3})
    st.sell("iPhone 15")
    assert st.get_stock("iPhone 15") == 1
    assert st.get_stock("Pixel 8") == 3  # untouched


def test_sell_sequence_tracks_correctly():
    """Simulate a sequence of sales like a real simulation would produce."""
    initial = {
        "iPhone 15": 5,
        "Samsung Galaxy A55": 12,
        "Google Pixel 8": 6,
    }
    st = StockTracker(initial)

    # Sell 3 iPhones
    for _ in range(3):
        assert st.sell("iPhone 15") is True
    assert st.get_stock("iPhone 15") == 2

    # Sell all Pixels
    for _ in range(6):
        assert st.sell("Google Pixel 8") is True
    assert st.get_stock("Google Pixel 8") == 0
    assert st.sell("Google Pixel 8") is False  # out of stock

    # A55 untouched
    assert st.get_stock("Samsung Galaxy A55") == 12


def test_concurrent_sell_same_product():
    """Two conversations try to buy the last unit."""
    st = StockTracker({"OnePlus 12": 1})
    assert st.sell("OnePlus 12") is True   # first sale succeeds
    assert st.sell("OnePlus 12") is False  # second fails — out of stock
    assert st.get_stock("OnePlus 12") == 0


def test_longest_match_wins_for_nested_names():
    """Variant suffix must decrement the more specific catalog key, not the prefix."""
    st = StockTracker({"Samsung Galaxy S24": 8, "Samsung Galaxy S24 Ultra": 4})
    assert st.sell("Samsung Galaxy S24 Ultra 256GB") is True
    assert st.get_stock("Samsung Galaxy S24") == 8
    assert st.get_stock("Samsung Galaxy S24 Ultra") == 3


def test_longest_match_pixel_pro():
    st = StockTracker({"Google Pixel 8": 6, "Google Pixel 8 Pro": 4})
    assert st.sell("Google Pixel 8 Pro 128GB") is True
    assert st.get_stock("Google Pixel 8") == 6
    assert st.get_stock("Google Pixel 8 Pro") == 3


def test_longest_match_iphone_pro_max():
    st = StockTracker({"iPhone 15": 5, "iPhone 15 Pro Max": 3})
    assert st.sell("iPhone 15 Pro Max black") is True
    assert st.get_stock("iPhone 15") == 5
    assert st.get_stock("iPhone 15 Pro Max") == 2


def test_canonical_returns_catalog_key():
    st = StockTracker({"Samsung Galaxy S24": 8, "Samsung Galaxy S24 Ultra": 4})
    assert st.canonical("Samsung Galaxy S24 Ultra 256GB") == "Samsung Galaxy S24 Ultra"
    assert st.canonical("samsung galaxy s24") == "Samsung Galaxy S24"
    assert st.canonical("Nokia 3310") is None


if __name__ == "__main__":
    import sys
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
            print(f"  ✓ {test.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  ✗ {test.__name__}: {e}")
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
