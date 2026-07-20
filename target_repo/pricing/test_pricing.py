from pricing import order_total, shipping_cost


def test_order_total_sums_line_items():
    assert order_total([(2.50, 4), (1.00, 3)]) == 13.00


def test_order_total_empty_order():
    assert order_total([]) == 0


def test_shipping_charged_below_threshold():
    assert shipping_cost(49.99) == 4.99


def test_shipping_free_at_threshold():
    # "free on orders of $50 or more" -> exactly $50 qualifies
    assert shipping_cost(50.00) == 0.0


def test_shipping_free_above_threshold():
    assert shipping_cost(75.00) == 0.0
