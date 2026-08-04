from __future__ import annotations

import argparse
import json

from lineage_api.config import Settings
from lineage_api.dependencies import build_services


def main() -> None:
    parser = argparse.ArgumentParser(description="Operate the local lineage prototype")
    parser.add_argument("command", choices=("reset",))
    args = parser.parse_args()
    if args.command == "reset":
        summary = build_services(Settings.from_environment()).reset()
        printable = {key: value for key, value in summary.items() if key != "demoDelivery"}
        print(json.dumps(printable, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
