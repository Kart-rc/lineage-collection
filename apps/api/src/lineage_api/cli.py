from __future__ import annotations

import argparse
import hashlib
import hmac
import json
from collections.abc import Sequence
from pathlib import Path

from lineage_api.application.repository_sources import (
    RepositoryCheckoutDescriptor,
    RepositorySourceError,
    RepositorySourceLimits,
)
from lineage_api.config import Settings
from lineage_api.dependencies import build_services
from lineage_api.infrastructure.local_git_source import LocalGitRepositorySource
from lineage_api.services.analyzer_registry import (
    AnalyzerRegistry,
    AnalyzerSelection,
    AnalyzerSelectionError,
    canonical_source_metadata,
    deterministic_checkout_event_id,
)
from lineage_api.services.intake import PushDelivery


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
        descriptor = RepositoryCheckoutDescriptor(
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
        ).snapshot(descriptor)
        metadata = canonical_source_metadata(snapshot, schema_profile=args.profile)
        settings = Settings.from_environment()
        services = build_services(settings, repository_snapshot=snapshot)
        payload = {
            "eventId": deterministic_checkout_event_id(
                metadata,
                snapshot.repository,
                snapshot.environment,
                snapshot.system,
            ),
            "eventType": "repo.push",
            "repo": snapshot.repository,
            "digest": snapshot.revision,
            "env": snapshot.environment,
            "system": snapshot.system,
            "changedFiles": list(snapshot.paths),
            "repositorySource": metadata,
            "receivedAt": "1970-01-01T00:00:00Z",
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        signature = hmac.new(
            settings.webhook_secret.encode(), canonical, hashlib.sha256
        ).hexdigest()
        result = services.orchestration.process_push(
            PushDelivery(payload, f"sha256={signature}")
        )
        summary = _checkout_summary(result, snapshot.scope_digest, snapshot.revision)
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


def _checkout_summary(
    result: dict[str, object], scope_digest: str, revision: str
) -> dict[str, object]:
    run = result.get("run") if isinstance(result.get("run"), dict) else {}
    proposal = (
        result.get("proposal") if isinstance(result.get("proposal"), dict) else {}
    )
    command = result.get("command") if isinstance(result.get("command"), dict) else {}
    analysis = result.get("analysis") if isinstance(result.get("analysis"), dict) else {}
    return {
        "outcome": result.get("outcome"),
        "reasonCode": result.get("reason"),
        "commandId": command.get("commandId"),
        "commandStatus": command.get("status"),
        "determinantDigest": command.get("determinantDigest"),
        "revision": revision,
        "scopeDigest": scope_digest,
        "runId": run.get("runId"),
        "runStatus": run.get("state"),
        "stages": [
            stage.get("stage")
            for stage in run.get("stages", [])
            if isinstance(stage, dict)
        ],
        "proposalId": proposal.get("proposalId"),
        "proposalStatus": proposal.get("state"),
        "runtimeStatus": result.get("runtimeStatus", "NOT_PROVIDED"),
        "analysisStatus": analysis.get("status"),
        "statusReasons": analysis.get("statusReasons", []),
        "counts": {
            "edges": analysis.get("edgeCount", 0),
            "reads": analysis.get("readCount", 0),
            "writes": analysis.get("writeCount", 0),
            "residue": analysis.get("residueCount", 0),
            "unresolved": analysis.get("unresolvedCount", 0),
        },
    }


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
