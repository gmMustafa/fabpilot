"""Completeness helpers of the data layer: format_duration and coverage_note (your TODO).

These sentences are what a model reads when a window and the data don't match (Run 3: "no alarms"
over a window that started after the last reading was reported as "healthy right now").
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.plant_data import coverage_note, format_duration


def t(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)


@pytest.mark.parametrize(
    ("td", "text"),
    [
        (timedelta(days=7, minutes=14), "7 d 0 h 14 min"),  # F6: was "7 days"
        (timedelta(hours=25, minutes=29), "1 d 1 h 29 min"),
        (timedelta(hours=3, minutes=5), "3 h 5 min"),
        (timedelta(minutes=14, seconds=59), "14 min"),
    ],
)
def test_format_duration(td: timedelta, text: str) -> None:
    assert format_duration(td) == text


# ------------------------------------------------------------------ coverage_note (your TODO)

DATA = (t(6, 0), t(13, 11, 7))  # M-05 in the dev DB: 2026-11-06 00:00 .. 2026-11-13 11:07


def test_window_fully_covered_needs_no_note() -> None:
    assert coverage_note(t(10, 0), t(12, 0), *DATA) is None


def test_small_lag_at_the_end_is_tolerated() -> None:
    """Live data is always a few seconds old: don't nag about that."""
    assert coverage_note(t(12, 0), t(13, 11, 10), *DATA) is None


def test_window_extends_past_the_data_end() -> None:
    note = coverage_note(t(11, 12, 36), t(14, 12, 36), *DATA)
    assert note == (
        "data ends 2026-11-13 11:07 UTC, 1 d 1 h 29 min before the end of the window: "
        "nothing can be said about the last 1 d 1 h 29 min."
    )


def test_window_entirely_after_the_data() -> None:
    """Run 3, Q3: the default 24 h alarm window started 1.5 h after M-05's last reading."""
    note = coverage_note(t(13, 12, 36), t(14, 12, 36), *DATA)
    assert note == (
        "the window starts after the last reading (2026-11-13 11:07 UTC): there is no data "
        "in it; nothing can be said about this period"
    )


def test_window_entirely_before_the_data() -> None:
    note = coverage_note(t(1, 0), t(2, 0), *DATA)
    assert (
        note
        == "the window ends before the first reading (2026-11-06 00:00 UTC): there is no data in it"
    )


def test_data_starts_late_and_ends_early_gives_both_sentences() -> None:
    note = coverage_note(t(5, 0), t(14, 0), *DATA)
    assert note is not None
    assert note.startswith("data ends 2026-11-13 11:07 UTC, 12 h 53 min before the end")
    assert note.endswith(
        "data starts 2026-11-06 00:00 UTC, 1 d 0 h 0 min after the start of the window."
    )


def test_data_starting_exactly_at_the_end_is_outside_the_half_open_window() -> None:
    assert coverage_note(t(5, 0), t(6, 0), *DATA) == (
        "the window ends before the first reading (2026-11-06 00:00 UTC): there is no data in it"
    )


def test_no_data_at_all() -> None:
    assert coverage_note(t(10, 0), t(11, 0), None, None) == (
        "no readings for this machine at all: nothing can be said about it"
    )


# ------------------------------------------------------------------ data age (F18)


def test_note_states_the_data_age_relative_to_now() -> None:
    """Run 4, Q8: a window ending in the FUTURE gave "12 h before the end of the window", and a
    model reported "silent for 12 h"; relative to now the data was ~26 h old."""
    note = coverage_note(t(6, 0), t(14, 0), *DATA, now=t(14, 13, 50))
    assert note is not None
    assert note.startswith("data ends 2026-11-13 11:07 UTC, 12 h 53 min before the end")
    assert note.endswith("The newest reading is 1 d 2 h 43 min old.")


def test_age_is_added_when_the_window_starts_after_the_data() -> None:
    note = coverage_note(t(13, 12, 36), t(14, 12, 36), *DATA, now=t(14, 12, 36))
    assert note is not None and note.endswith("The newest reading is 1 d 1 h 29 min old.")


def test_no_age_without_now_or_when_the_data_is_fresh() -> None:
    assert "old" not in (coverage_note(t(6, 0), t(14, 0), *DATA) or "")  # now not given
    fresh = coverage_note(t(6, 0), t(14, 0), *DATA, now=t(13, 11, 9))  # 2 min old: tolerated
    assert fresh is not None and "old" not in fresh
