from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from lineage_api.application.repository_collection import (
    AnalyzerIdentity,
    RepositoryCollectionDescriptor,
    RepositoryIdentity,
)
from lineage_api.application.repository_sources import (
    RepositoryCheckoutDescriptor,
    RepositorySourceError,
    RepositorySourceLimits,
)
from lineage_api.config import Settings
from lineage_api.dependencies import build_repository_collection_service, build_services
from lineage_api.infrastructure.local_git_source import LocalGitRepositorySource
from lineage_api.services.analyzer_registry import (
    AnalyzerRegistry,
    AnalyzerSelection,
    AnalyzerSelectionError,
)


def run(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Operate the local lineage prototype")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("reset", help="Reset deterministic local demo state")
    worker = subcommands.add_parser("worker", help="Process durable lineage commands")
    mode = worker.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="Process at most one message")
    mode.add_argument("--drain", action="store_true", help="Process messages until empty or bounded")
    worker.add_argument("--max-messages", type=int, default=100)
    checkout = subcommands.add_parser(
        "collect-checkout",
        help="Collect static lineage from a bounded exact local Git checkout",
    )
    checkout.add_argument("--checkout", required=True)
    checkout.add_argument("--origin", required=True)
    checkout.add_argument("--revision", required=True)
    checkout.add_argument("--repository", required=True)
    checkout.add_argument("--environment", required=True)
    checkout.add_argument("--platform", required=True)
    checkout.add_argument("--system", required=True)
    checkout.add_argument("--analyzer-pack", required=True)
    checkout.add_argument("--ruleset", required=True)
    checkout.add_argument("--profile", required=True)
    args = parser.parse_args(argv)
    if args.command == "collect-checkout":
        return _collect_checkout(args)
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


def _collect_checkout(args: argparse.Namespace) -> int:
    try:
        _validate_checkout_argument_bounds(args)
        selection = AnalyzerSelection(
            analyzer_pack=args.analyzer_pack,
            ruleset=args.ruleset,
            source_kind="git-checkout",
            framework="spring-data-jpa",
            schema_profile=args.profile,
        )
        AnalyzerRegistry.default().resolve(selection)
        if args.platform != args.profile:
            raise AnalyzerSelectionError(
                "PROFILE_PLATFORM_MISMATCH",
                "platform and schema profile must match for exact checkout collection",
            )
        requested_checkout = Path(args.checkout)
        if requested_checkout.is_symlink():
            raise ValueError("checkout root must not be a symlink")
        canonical_checkout = requested_checkout.resolve(strict=True)
        checkout_descriptor = RepositoryCheckoutDescriptor(
            origin=args.origin,
            repository=args.repository,
            revision=args.revision,
            checkout_root=canonical_checkout,
            environment=args.environment,
            platform=args.platform,
            system=args.system,
            analyzer_pack=args.analyzer_pack,
            ruleset=args.ruleset,
        )
        snapshot = LocalGitRepositorySource(
            RepositorySourceLimits(
                max_files=10_000,
                max_file_bytes=4 * 1024 * 1024,
                max_total_bytes=128 * 1024 * 1024,
            )
        ).snapshot(checkout_descriptor)
        settings = Settings.from_environment()
        collection_descriptor = RepositoryCollectionDescriptor(
            repository=RepositoryIdentity(
                origin=snapshot.origin,
                repository=snapshot.repository,
                revision=snapshot.revision,
                environment=snapshot.environment,
                platform=snapshot.platform,
                system=snapshot.system,
            ),
            analyzer=AnalyzerIdentity(
                analyzer_pack=snapshot.analyzer_pack,
                ruleset=snapshot.ruleset,
                source_kind="git-checkout",
                framework="spring-data-jpa",
                schema_profile=args.profile,
            ),
            snapshot=snapshot,
        )
        result = build_repository_collection_service(settings).collect(
            collection_descriptor
        )
        summary = {
            key: value
            for key, value in result.items()
            if key not in {"collectionId", "statusUrl"}
        }
        print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
        return 0 if result["outcome"] in {"ACCEPTED", "DUPLICATE", "REUSED"} else 2
    except AnalyzerSelectionError as error:
        _print_checkout_error(error.code)
        return 2
    except RepositorySourceError:
        _print_checkout_error("SOURCE_VALIDATION_FAILED")
        return 2
    except (TypeError, ValueError):
        _print_checkout_error("INVALID_CHECKOUT_DESCRIPTOR")
        return 2
    except Exception:
        _print_checkout_error("PIPELINE_FAILED")
        return 2


def _print_checkout_error(code: str) -> None:
    print(
        json.dumps(
            {"errorCode": code, "outcome": "INVALID"},
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _validate_checkout_argument_bounds(args: argparse.Namespace) -> None:
    bounds = {
        "checkout": 4_096,
        "origin": 2_048,
        "revision": 64,
        "repository": 128,
        "environment": 128,
        "platform": 128,
        "system": 128,
        "analyzer_pack": 128,
        "ruleset": 128,
        "profile": 128,
    }
    for name, limit in bounds.items():
        value = getattr(args, name)
        if (
            not isinstance(value, str)
            or not value
            or len(value.encode()) > limit
            or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("checkout argument exceeds its closed bound")


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
