"""Canonical product matching shared by stock, evaluation, viewer."""

from typing import Iterable, Optional


def find_canonical(product: str, keys: Iterable[str]) -> Optional[str]:
    """Map a free-form product name to a canonical key.

    Resolution order: exact -> case-insensitive exact -> longest substring match.
    Prefers the most specific (longest) catalog key when names are nested
    (e.g. "Samsung Galaxy S24 Ultra 256GB" -> "Samsung Galaxy S24 Ultra",
    not "Samsung Galaxy S24").
    """
    if not product:
        return None
    keys = list(keys)
    if product in keys:
        return product
    product_lower = product.lower()
    for key in keys:
        if key.lower() == product_lower:
            return key
    best_key = None
    best_len = 0
    for key in keys:
        key_lower = key.lower()
        if key_lower in product_lower or product_lower in key_lower:
            if len(key) > best_len:
                best_len = len(key)
                best_key = key
    return best_key
