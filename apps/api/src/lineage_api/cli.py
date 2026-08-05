from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from lineage_api.config import Settings
from lineage_api.dependencies import build_services


def run(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Operate the local lineage prototype")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("reset", help="Reset deterministic local demo state")
    worker = subcommands.add_parser("worker", help="Process durable lineage commands")
    mode = worker.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="Process at most one message")
    mode.add_argument("--drain", action="store_true", help="Process messages until empty or bounded")
    worker.add_argument("--max-messages", type=int, default=100)
    args = parser.parse_args(argv)
    services = build_services(Settings.from_environment())
    if args.command == "reset":
        summary = services.reset()
        printable = {key: value for key, value in summary.items() if key != "demoDelivery"}
        print(json.dumps(printable, indent=2, sort_keys=True))
        return 0

    if args.max_messages < 1:
        parser.error("--max-messages must be positive")
    if args.once:
        result = services.orchestration.worker_once()
        results = [] if result is None else [result]
    else:
        results = services.orchestration.worker_drain(max_messages=args.max_messages)
    print(json.dumps({"processed": len(results), "results": results}, indent=2, sort_keys=True))
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
