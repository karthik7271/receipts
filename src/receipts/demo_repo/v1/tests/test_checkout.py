from tinyshop.checkout import total
from tinyshop.money import Money
from tinyshop.refunds import refund_amount


def test_discount_applied_before_tax():
    # $100 with 10% off -> $90.00 + 8% tax ($7.20) = $97.20
    assert total([Money.from_dollars("100.00")], discount_percent=10) == Money.from_dollars("97.20")


def test_partial_refund_rounds_to_cent():
    assert refund_amount(Money.from_dollars("10.00"), 1 / 3) == Money(333)
