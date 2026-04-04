"""Data structures for Sales Arena."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Turn:
    """A single message in a conversation."""

    role: str  # "consumer" or "seller"
    content: str
    turn_number: int


@dataclass
class Conversation:
    """A full conversation between seller and consumer."""

    id: str
    consumer_profile: str
    turns: list[Turn] = field(default_factory=list)
    outcome: str = "pending"  # "sale", "no_sale", "timeout"
    sale_details: Optional[dict] = None  # {"product": ..., "price": ..., "cost": ...}
    status: str = "active"  # "active", "finished"

    @property
    def last_message(self) -> Optional[str]:
        if self.turns:
            return self.turns[-1].content
        return None

    @property
    def current_turn(self) -> int:
        return max((t.turn_number for t in self.turns), default=0)

    def summary(self) -> str:
        """One-line status summary for seller context."""
        if self.outcome == "sale":
            product = self.sale_details.get("product", "?") if self.sale_details else "?"
            price = self.sale_details.get("price", "?") if self.sale_details else "?"
            return f"{self.id}: vendido {product} por ${price}, cerrado"
        if self.status == "finished":
            return f"{self.id}: no compró, cerrado"

        last = self.turns[-1].content[:80] if self.turns else "sin mensajes"
        return f"{self.id} (turno {self.current_turn}): activo, último mensaje: {last}"


@dataclass
class Violation:
    """A constraint violation detected by the judge."""

    conversation_id: str
    constraint: str
    description: str
    severity: str = "critical"  # "critical" or "warning"


@dataclass
class ExperimentResult:
    """Results from a single experiment run."""

    experiment_id: str
    timestamp: str
    model: str
    model_params: dict
    seller_prompt: str
    total_profit: float
    total_revenue: float
    valid_sales: int
    invalid_sales: int
    no_sales: int
    total_conversations: int
    violations: list[Violation]
    analysis: str
    conversations: list[Conversation]
    total_tokens: int = 0
