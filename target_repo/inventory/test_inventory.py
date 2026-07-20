from inventory import needs_reorder, reorder_list, total_stock


def item(name, stock, reorder_point, discontinued=False):
    return {
        "name": name,
        "stock": stock,
        "reorder_point": reorder_point,
        "discontinued": discontinued,
    }


def test_needs_reorder_when_below_point():
    assert needs_reorder(item("bolt", stock=2, reorder_point=5)) is True


def test_needs_reorder_at_point():
    assert needs_reorder(item("bolt", stock=5, reorder_point=5)) is True


def test_no_reorder_when_well_stocked():
    assert needs_reorder(item("bolt", stock=40, reorder_point=5)) is False


def test_no_reorder_when_discontinued():
    # Discontinued items are never reordered, even at zero stock
    assert needs_reorder(item("clip", stock=0, reorder_point=5, discontinued=True)) is False


def test_reorder_list_is_sorted():
    items = [
        item("washer", stock=1, reorder_point=5),
        item("bolt", stock=0, reorder_point=5),
        item("nut", stock=99, reorder_point=5),
    ]
    assert reorder_list(items) == ["bolt", "washer"]


def test_total_stock():
    assert total_stock([item("bolt", 3, 5), item("nut", 7, 5)]) == 10
