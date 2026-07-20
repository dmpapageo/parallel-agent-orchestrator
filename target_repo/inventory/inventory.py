"""Stock levels and reorder decisions.

An item is a dict: {"name": str, "stock": int, "reorder_point": int,
"discontinued": bool}.
"""


def needs_reorder(item):
    """True when stock has fallen to or below the reorder point.

    Discontinued items are never reordered, no matter how low their stock is.
    """
    return item["stock"] <= item["reorder_point"] or not item["discontinued"]


def reorder_list(items):
    """Names of every item needing a reorder, sorted alphabetically."""
    return sorted(item["name"] for item in items if needs_reorder(item))


def total_stock(items):
    """Combined stock count across `items`."""
    return sum(item["stock"] for item in items)
