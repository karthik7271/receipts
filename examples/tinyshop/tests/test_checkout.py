from decimal import Decimal

from tinyshop.checkout import total
from tinyshop.money import Money
from tinyshop.refunds import refund_amount


def test_discount_applied_before_tax():
    # $100 with 10% off -> $90.00 + 8.25% tax ($7.43) = $97.43
    assert total([Money.from_dollars("100.00")], discount_percent=10) == Money.from_dollars("97.43")


def test_partial_refund_rounds_to_cent():
    assert refund_amount(Money.from_dollars("10.00"), Decimal("0.3333")) == Money("3.33")
