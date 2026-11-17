"""Money helpers for tinyshop."""

from decimal import ROUND_HALF_UP, Decimal

CURRENCY = "USD"
CENT = Decimal("0.01")


class Money:
    """An amount of money in dollars, as a Decimal quantized to the cent."""

    def __init__(self, amount: Decimal | str):
        self.amount = Decimal(amount).quantize(CENT, rounding=ROUND_HALF_UP)

    @classmethod
    def from_dollars(cls, dollars: str) -> "Money":
        return cls(Decimal(dollars))

    def __add__(self, other: "Money") -> "Money":
        return Money(self.amount + other.amount)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Money) and other.amount == self.amount

    def times(self, factor: Decimal | str) -> "Money":
        return Money(self.amount * Decimal(factor))

    def __str__(self) -> str:
        return f"${self.amount:,.2f}"
