"""FabPilot plant simulator.

uv run simulator backfill --days 7                        # history, idempotent
uv run simulator run                                      # live readings every 10 s
uv run simulator inject --fault bearing_wear --machine M-01 [--duration 3d]
uv run simulator inject --fault sensor_drift --machine M-08 --sensor discharge_temp
uv run simulator clear --machine M-01                     # repaired: fault ends now
"""

import argparse
import logging
import re
import time
from datetime import datetime, timedelta

from app.config import get_settings
from app.db.models import FaultType
from app.plant import load_plant
from simulator.db import connect
from simulator.faults import DEFAULT_DURATION
from simulator.runner import backfill, clear, inject, run_live

DEFAULT_SEED = 42
_DURATION = re.compile(r"(\d+)([smhd])")
_UNIT = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def parse_duration(text: str) -> timedelta:
    """'3d', '30m', '1h30m' -> timedelta. Strict: the whole string must be <number><unit>..."""
    parts = _DURATION.findall(text)
    if not parts or "".join(n + u for n, u in parts) != text:
        raise argparse.ArgumentTypeError(
            f"invalid duration {text!r} (examples: 30m, 2h, 3d, 1h30m)"
        )
    return sum((timedelta(**{_UNIT[u]: int(n)}) for n, u in parts), timedelta())


def _utc(text: str) -> datetime:
    t = datetime.fromisoformat(text)
    if t.tzinfo is None:
        raise argparse.ArgumentTypeError("--start needs a timezone, e.g. 2026-11-12T06:00+00:00")
    return t


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="simulator", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="same seed = same data")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("backfill", help="write N days of history up to now")
    p.add_argument("--days", type=int, default=7)

    p = sub.add_parser("run", help="stream live readings until Ctrl+C")
    p.add_argument("--interval", type=float, default=10.0, help="seconds between ticks")

    defaults = ", ".join(f"{t.value}={d}" for t, d in DEFAULT_DURATION.items())
    p = sub.add_parser("inject", help="inject a known fault (ground truth for evals)")
    p.add_argument("--fault", type=FaultType, choices=list(FaultType), required=True)
    p.add_argument("--machine", required=True, help="machine code, e.g. M-07")
    p.add_argument("--duration", type=parse_duration, help=f"time to develop (default: {defaults})")
    p.add_argument("--start", type=_utc, help="ISO time with tz (default: now - duration)")
    p.add_argument("--severity", type=float, default=1.0)
    p.add_argument("--sensor", help="sensor_drift only (default: a temperature sensor)")

    p = sub.add_parser("clear", help="repair: end the machine's active faults now")
    p.add_argument("--machine", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(levelname)s %(message)s")
    log = logging.getLogger("simulator")
    plant = load_plant(settings.plant_file)  # validate before touching the DB

    if args.command is None:
        print(f"Plant loaded: {len(plant.machines)} machines. No command given; see --help.")
        return 0

    with connect(settings) as conn:
        t0 = time.perf_counter()
        match args.command:
            case "backfill":
                rows = backfill(conn, plant, days=args.days, seed=args.seed)
                log.info("Wrote %s readings in %.1f s", f"{rows:,}", time.perf_counter() - t0)
            case "run":
                run_live(conn, plant, seed=args.seed, interval=args.interval)
            case "inject":
                r = inject(
                    conn,
                    plant,
                    args.fault,
                    args.machine,
                    seed=args.seed,
                    duration=args.duration,
                    start=args.start,
                    severity=args.severity,
                    sensor=args.sensor,
                )
                log.info(
                    "Injected %s on %s (fault #%d): %s -> fully developed %s%s",
                    r.fault.type.value,
                    r.fault.machine_code,
                    r.fault_id,
                    r.fault.start.isoformat(timespec="minutes"),
                    r.fault.full_at.isoformat(timespec="minutes"),
                    f", sensor {r.fault.sensor}" if r.fault.sensor else "",
                )
                log.info(
                    "Rewrote %s readings, %d alarms%s",
                    f"{r.rows:,}",
                    r.alarms,
                    ". Machine TRIPPED (status: down)" if r.tripped else "",
                )
            case "clear":
                n = clear(conn, args.machine)
                log.info("Cleared %d active fault(s) on %s; back in service", n, args.machine)
    return 0
