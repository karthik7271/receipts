"""Discounts and tax."""

from .money import Money

MAX_DISCOUNT_PERCENT = 50


def apply_discount(subtotal: Money, percent: int) -> Money:
    """Discounts apply to the pre-tax subtotal and are capped."""
    if not 0 <= percent <= MAX_DISCOUNT_PERCENT:
        raise ValueError(f"discounts are capped at {MAX_DISCOUNT_PERCENT}%")
    return subtotal.times(1 - percent / 100)


def tax_for(amount: Money, rate: float) -> Money:
    return amount.times(rate)
