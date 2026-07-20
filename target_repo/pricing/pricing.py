"""Order pricing and shipping rules."""

FREE_SHIPPING_THRESHOLD = 50.00
FLAT_RATE = 4.99


def order_total(items):
    """Total cost of `items`, a list of (unit_price, quantity) pairs."""
    return round(sum(price * quantity for price, quantity in items), 2)


def shipping_cost(total):
    """Shipping is free on orders of $50 or more; otherwise a flat $4.99."""
    if total > FREE_SHIPPING_THRESHOLD:
        return 0.0
    return FLAT_RATE
