"""Order totals."""

from .money import Money
from .pricing import apply_discount, tax_for
from .settings import load_settings


def total(items: list[Money], discount_percent: int = 0) -> Money:
    """Discounted subtotal plus tax on the discounted amount."""
    subtotal = sum(items, Money(0))
    discounted = apply_discount(subtotal, discount_percent)
    rate = load_settings()["tax_rate"]
    return discounted + tax_for(discounted, rate)
