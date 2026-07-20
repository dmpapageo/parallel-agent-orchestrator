from text_stats import word_count, initials, truncate


def test_word_count():
    assert word_count("the quick brown fox") == 4


def test_word_count_empty():
    assert word_count("   ") == 0


def test_initials():
    assert initials("ada lovelace") == "AL"


def test_truncate_leaves_short_text_alone():
    assert truncate("hello", 10) == "hello"


def test_truncate_at_exact_length():
    assert truncate("hello", 5) == "hello"


def test_truncate_result_fits_in_max_length():
    # 20 chars in, max 10 out -> the result INCLUDING "..." is 10 chars
    result = truncate("abcdefghijklmnopqrst", 10)
    assert len(result) == 10
    assert result == "abcdefg..."
