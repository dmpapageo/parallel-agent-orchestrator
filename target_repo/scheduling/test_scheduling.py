from scheduling import duration, busy_hours, free_slots


def test_duration():
    assert duration((9, 11)) == 2


def test_busy_hours():
    assert busy_hours([(9, 11), (13, 14)]) == 3


def test_free_slots_empty_day():
    assert free_slots([], 9, 17) == [(9, 17)]


def test_free_slots_between_bookings():
    assert free_slots([(10, 11), (13, 14)], 9, 17) == [(9, 10), (11, 13), (14, 17)]


def test_free_slots_before_first_booking():
    # The morning gap from 9 to 10 is free and must be reported
    assert free_slots([(10, 12)], 9, 17) == [(9, 10), (12, 17)]


def test_free_slots_booking_starts_at_day_start():
    # Nothing free before a booking that opens the day
    assert free_slots([(9, 12)], 9, 17) == [(12, 17)]
