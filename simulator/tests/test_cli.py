import argparse
from datetime import timedelta

import pytest

from simulator.cli import main, parse_duration


def test_cli_loads_plant_from_api_package(capsys: pytest.CaptureFixture[str]) -> None:
    """Smoke test for the workspace wiring: the simulator can import and use the API package."""
    assert main([]) == 0
    assert "Plant loaded: 10 machines" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("30m", timedelta(minutes=30)),
        ("2h", timedelta(hours=2)),
        ("3d", timedelta(days=3)),
        ("1h30m", timedelta(hours=1, minutes=30)),
    ],
)
def test_parse_duration(text: str, expected: timedelta) -> None:
    assert parse_duration(text) == expected


@pytest.mark.parametrize("text", ["", "3", "d3", "3x", "3d junk", "-2h"])
def test_parse_duration_rejects_garbage(text: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        parse_duration(text)
