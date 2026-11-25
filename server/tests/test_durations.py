import pytest
from hypothesis import given
from hypothesis import strategies as st

from syncbaton.durations import parse_duration

UNITS = {"s": 1, "m": 60, "h": 3600}


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("30s", 30), ("15m", 900), ("2h", 7200), (" 5m ", 300), ("1 m", 60)],
)
def test_parses_examples(text, seconds):
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["", "15", "m", "0s", "-5m", "1.5h", "5d", "5 minutes", "1m30s"])
def test_rejects_bad_durations(text):
    with pytest.raises(ValueError, match="duration"):
        parse_duration(text)


@given(st.integers(min_value=1, max_value=10**6), st.sampled_from(sorted(UNITS)))
def test_number_times_unit(n, unit):
    assert parse_duration(f"{n}{unit}") == n * UNITS[unit]
