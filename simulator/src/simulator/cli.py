"""FabPilot plant simulator.

uv run simulator backfill --days 7     # history, idempotent
uv run simulator run                   # live: new readings every 10 s (Ctrl+C to stop)
"""

import argparse
import logging
import time

from app.config import get_settings
from app.plant import load_plant
from simulator.db import connect
from simulator.runner import backfill, run_live

DEFAULT_SEED = 42


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="simulator", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="same seed = same data")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    p_back = sub.add_parser("backfill", help="write N days of history up to now")
    p_back.add_argument("--days", type=int, default=7)
    p_run = sub.add_parser("run", help="stream live readings until Ctrl+C")
    p_run.add_argument("--interval", type=float, default=10.0, help="seconds between ticks")
    args = parser.parse_args(argv)

    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(levelname)s %(message)s")
    plant = load_plant(settings.plant_file)  # validate before touching the DB

    if args.command is None:
        print(f"Plant loaded: {len(plant.machines)} machines. No command given; see --help.")
        return 0

    with connect(settings) as conn:
        if args.command == "backfill":
            t0 = time.perf_counter()
            rows = backfill(conn, plant, days=args.days, seed=args.seed)
            sensors = sum(len(plant.sensors_for(m)) for m in plant.machines)
            logging.info(
                "Wrote %s readings for %d sensors in %.1f s",
                f"{rows:,}",
                sensors,
                time.perf_counter() - t0,
            )
        elif args.command == "run":
            run_live(conn, plant, seed=args.seed, interval=args.interval)
    return 0
