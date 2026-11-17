"""Refunds."""

from .money import Money


def refund_amount(paid: Money, returned_fraction: float) -> Money:
    """Partial refunds are prorated and rounded to the nearest cent."""
    # Guard against nonsense fractions (zero, negative, or more than everything).
    if not 0 < returned_fraction <= 1:
        raise ValueError(
            "returned_fraction must be in (0, 1]"
        )
    return paid.times(returned_fraction)
