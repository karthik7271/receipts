"""Promotions: discounts live here now."""

from .money import Money

MAX_DISCOUNT_PERCENT = 50


def discount_for(subtotal: Money, percent: int) -> Money:
    """Discounts apply to the pre-tax subtotal and are capped."""
    if not 0 <= percent <= MAX_DISCOUNT_PERCENT:
        raise ValueError(f"discounts are capped at {MAX_DISCOUNT_PERCENT}%")
    return subtotal.times(1 - percent / 100)
