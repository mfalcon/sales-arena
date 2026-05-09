"""Catalog stock tracker."""

from arena.products import find_canonical


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
                lines.append(f"- {product}: {qty} available")
            else:
                lines.append(f"- {product}: OUT OF STOCK")
        return "\n".join(lines) if lines else "No products in stock."

    def snapshot(self) -> dict[str, int]:
        """Current stock as a dict."""
        return dict(self._stock)

    def canonical(self, product: str) -> str | None:
        """Return the canonical catalog key for a free-form product name."""
        return self._find_product(product)

    def _find_product(self, product: str) -> str | None:
        """Find product key with canonical fuzzy matching (longest match wins)."""
        return find_canonical(product, self._stock.keys())
