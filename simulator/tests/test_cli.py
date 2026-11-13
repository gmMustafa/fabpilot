import pytest

from simulator.cli import main


def test_cli_loads_plant_from_api_package(capsys: pytest.CaptureFixture[str]) -> None:
    """Smoke test for the workspace wiring: the simulator can import and use the API package."""
    assert main([]) == 0
    assert "Plant loaded: 10 machines" in capsys.readouterr().out
