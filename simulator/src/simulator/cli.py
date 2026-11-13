"""Command-line entry point: `uv run simulator ...`. Commands are added in 1.3c / 1.4."""

import argparse

from app.config import get_settings
from app.plant import load_plant


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="simulator", description=__doc__)
    parser.add_subparsers(dest="command", metavar="COMMAND")
    args = parser.parse_args(argv)
    if args.command is None:
        plant = load_plant(get_settings().plant_file)
        print(f"Plant loaded: {len(plant.machines)} machines. No command given; see --help.")
    return 0
