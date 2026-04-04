"""Catalog stock tracker."""


class StockTracker:
    """Tracks product stock during a simulation. Stock decreases with each sale."""

    def __init__(self, initial_stock: dict[str, int]):
        """Initialize with product -> quantity mapping."""
        self._stock = dict(initial_stock)
        self._initial = dict(initial_stock)

    def sell(self, product: str, quantity: int = 1) -> bool:
        """Attempt to sell a product. Returns True if successful, False if out of stock."""
        # Fuzzy match: try exact first, then case-insensitive
        key = self._find_product(product)
        if key is None:
            return False
        if self._stock[key] < quantity:
            return False
        self._stock[key] -= quantity
        return True

    def get_stock(self, product: str) -> int:
        """Get current stock for a product."""
        key = self._find_product(product)
        if key is None:
            return 0
        return self._stock[key]

    def get_stock_text(self) -> str:
        """Human-readable stock status for seller context."""
        lines = []
        for product, qty in self._stock.items():
            if qty > 0:
                lines.append(f"- {product}: {qty} disponible(s)")
            else:
                lines.append(f"- {product}: AGOTADO")
        return "\n".join(lines) if lines else "No hay productos en stock."

    def snapshot(self) -> dict[str, int]:
        """Current stock as a dict."""
        return dict(self._stock)

    def _find_product(self, product: str) -> str | None:
        """Find product key with fuzzy matching.

        Tries: exact → case-insensitive → substring containment.
        Handles cases like 'iPhone 15 en negro' matching 'iPhone 15'.
        """
        if product in self._stock:
            return product
        product_lower = product.lower()
        # Exact case-insensitive
        for key in self._stock:
            if key.lower() == product_lower:
                return key
        # Product name contains stock key or vice versa
        for key in self._stock:
            if key.lower() in product_lower or product_lower in key.lower():
                return key
        return None
