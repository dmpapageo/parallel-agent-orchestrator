"""Meeting-room scheduling helpers.

Times are whole hours on a 24-hour clock (9 = 9am, 17 = 5pm).
A booking is a (start, end) pair.
"""


def duration(booking):
    """Length of a booking in hours."""
    start, end = booking
    return end - start


def busy_hours(bookings):
    """Total hours booked across `bookings`."""
    return sum(duration(b) for b in bookings)


def free_slots(bookings, day_start, day_end):
    """Return the free (start, end) gaps in a day, in chronological order.

    Covers the gap before the first booking, the gaps between bookings, and the
    gap after the last booking. Zero-length gaps are omitted.
    """
    ordered = sorted(bookings)
    if not ordered:
        return [(day_start, day_end)]

    slots = []

    for earlier, later in zip(ordered, ordered[1:]):
        gap_start, gap_end = earlier[1], later[0]
        if gap_start < gap_end:
            slots.append((gap_start, gap_end))

    if ordered[-1][1] < day_end:
        slots.append((ordered[-1][1], day_end))

    return slots
