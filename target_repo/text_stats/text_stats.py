"""Small text measurement and formatting helpers."""

ELLIPSIS = "..."


def word_count(text):
    """Number of whitespace-separated words in `text`."""
    return len(text.split())


def initials(full_name):
    """Uppercase initials for each word in `full_name`, e.g. "ada lovelace" -> "AL"."""
    return "".join(word[0].upper() for word in full_name.split())


def truncate(text, max_length):
    """Shorten `text` to at most `max_length` characters.

    If `text` is longer than `max_length`, the result ends with "..." and the
    whole result (ellipsis included) is exactly `max_length` characters.
    """
    if len(text) <= max_length:
        return text
    return text[:max_length] + ELLIPSIS
