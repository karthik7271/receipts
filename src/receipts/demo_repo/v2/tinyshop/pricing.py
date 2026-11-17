"""Tax."""

from .money import Money


def tax_for(amount: Money, rate: float) -> Money:
    return amount.times(rate)
