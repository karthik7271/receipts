"""Money helpers for tinyshop."""

CURRENCY = "USD"


class Money:
    """An amount of money, stored as integer cents."""

    def __init__(self, cents: int):
        if not isinstance(cents, int):
            raise TypeError("Money is stored as integer cents")
        self.cents = cents

    @classmethod
    def from_dollars(cls, dollars: str) -> "Money":
        whole, _, frac = dollars.partition(".")
        return cls(int(whole) * 100 + int((frac + "00")[:2]))

    def __add__(self, other: "Money") -> "Money":
        return Money(self.cents + other.cents)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Money) and other.cents == self.cents

    def times(self, factor: float) -> "Money":
        return Money(round(self.cents * factor))

    def __str__(self) -> str:
        return f"${self.cents // 100}.{self.cents % 100:02d}"
