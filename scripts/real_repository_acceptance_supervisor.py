#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath


_CHECKSUM = re.compile(r"sha256:[0-9a-f]{64}")
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{2,79}")
_SAFE_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_PASS_KEYS = frozenset(
    {
        "counts",
        "evidenceClass",
        "manifestChecksum",
        "manifestPath",
        "outcome",
        "runtimeStatus",
    }
)
_PASS_COUNTS = {
    "edges": 15,
    "reads": 10,
    "residue": 8,
    "unresolved": 0,
    "writes": 5,
}
_FAILURE_KEYS = frozenset({"evidenceClass", "outcome", "reasonCode"})
_REQUIRED_FAILURE_REASONS = frozenset(
    {
        "LINEAGE_REAL_REPOSITORY_CHECKOUT_REQUIRED",
        "SOURCE_VALIDATION_FAILED",
        "INVALID_ACCEPTANCE_OUTPUT",
    }
)
_FAILURE_REASONS = frozenset({"PIPELINE_OR_ORACLE_FAILED"})
_TERMINATION_GRACE_SECONDS = 0.25
_TERMINATION_POLL_SECONDS = 0.01
_DIRECT_CHILD_REAP_SECONDS = 0.25


class SupervisorError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _bounded_environment_value(source: Mapping[str, str], name: str, limit: int) -> str | None:
    value = source.get(name)
    if value is None:
        return None
    if not value or len(value.encode()) > limit or any(ord(character) < 32 for character in value):
        raise SupervisorError("INVALID_ENVIRONMENT")
    return value


def _child_environment(source: Mapping[str, str]) -> dict[str, str]:
    checkout = _bounded_environment_value(source, "LINEAGE_REAL_REPOSITORY_CHECKOUT", 4096)
    if checkout is None:
        raise SupervisorError("CHECKOUT_REQUIRED")
    child = {
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "LINEAGE_REAL_REPOSITORY_CHECKOUT": checkout,
    }
    output = _bounded_environment_value(source, "LINEAGE_ACCEPTANCE_OUTPUT", 4096)
    if output is not None:
        child["LINEAGE_ACCEPTANCE_OUTPUT"] = output
    run_id = _bounded_environment_value(source, "LINEAGE_ACCEPTANCE_RUN_ID", 80)
    if run_id is not None:
        if _RUN_ID.fullmatch(run_id) is None:
            raise SupervisorError("INVALID_ENVIRONMENT")
        child["LINEAGE_ACCEPTANCE_RUN_ID"] = run_id
    return child


def _process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # macOS reports EPERM when the group contains only non-signalable
        # exited members; a live descendant created here retains our uid.
        return False
    return True


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    process_group_id = process.pid
    try:
        os.killpg(process_group_id, signal.SIGTERM)
    except ProcessLookupError:
        group_exists = False
    else:
        group_exists = True

    deadline = time.monotonic() + _TERMINATION_GRACE_SECONDS
    while group_exists:
        process.poll()
        group_exists = _process_group_exists(process_group_id)
        remaining = deadline - time.monotonic()
        if not group_exists or remaining <= 0:
            break
        time.sleep(min(_TERMINATION_POLL_SECONDS, remaining))

    if group_exists:
        try:
            os.killpg(process_group_id, signal.SIGKILL)
        except ProcessLookupError:
            pass

    try:
        process.wait(timeout=_DIRECT_CHILD_REAP_SECONDS)
    except subprocess.TimeoutExpired:
        pass


def _run_bounded_child(
    argv: Sequence[str],
    environment: Mapping[str, str],
    *,
    timeout_seconds: float,
    stdout_limit: int,
    stderr_limit: int,
    cwd: Path | None = None,
) -> tuple[int, bytes, bytes]:
    if timeout_seconds <= 0 or stdout_limit < 1 or stderr_limit < 1:
        raise ValueError("supervisor bounds must be positive")
    process: subprocess.Popen[bytes] | None = None
    selector = selectors.DefaultSelector()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    limits = {"stdout": stdout_limit, "stderr": stderr_limit}
    try:
        process = subprocess.Popen(
            list(argv),
            cwd=cwd,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            close_fds=True,
            start_new_session=True,
        )
        if process.stdout is None or process.stderr is None:
            raise SupervisorError("RUNNER_ENVIRONMENT_UNAVAILABLE")
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        deadline = time.monotonic() + timeout_seconds
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SupervisorError("TIMEOUT")
            events = selector.select(remaining)
            if not events:
                raise SupervisorError("TIMEOUT")
            for key, _mask in events:
                name = str(key.data)
                buffer = buffers[name]
                chunk = os.read(key.fd, min(64 * 1024, limits[name] - len(buffer) + 1))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                buffer.extend(chunk)
                if len(buffer) > limits[name]:
                    raise SupervisorError("OUTPUT_LIMIT")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SupervisorError("TIMEOUT")
        try:
            return_code = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired as error:
            raise SupervisorError("TIMEOUT") from error
        return return_code, bytes(buffers["stdout"]), bytes(buffers["stderr"])
    except SupervisorError:
        if process is not None:
            _terminate_process_group(process)
        raise
    except (OSError, subprocess.SubprocessError) as error:
        if process is not None:
            _terminate_process_group(process)
        raise SupervisorError("RUNNER_ENVIRONMENT_UNAVAILABLE") from error
    finally:
        selector.close()
        if process is not None:
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()


def _no_duplicate_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SupervisorError("INVALID_PASS")
        result[key] = value
    return result


def _one_json_object(content: bytes) -> dict[str, object]:
    try:
        text = content.decode("utf-8")
        decoder = json.JSONDecoder(object_pairs_hook=_no_duplicate_object)
        document, end = decoder.raw_decode(text.lstrip())
        if text.lstrip()[end:].strip():
            raise SupervisorError("INVALID_PASS")
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SupervisorError("INVALID_PASS") from error
    if not isinstance(document, dict):
        raise SupervisorError("INVALID_PASS")
    return document


def _validate_pass_document(content: bytes) -> dict[str, object]:
    document = _one_json_object(content)
    if set(document) != _PASS_KEYS:
        raise SupervisorError("INVALID_PASS")
    counts = document.get("counts")
    if (
        document.get("evidenceClass") != "LOCAL_REAL_REPOSITORY_PASS"
        or document.get("outcome") != "PASS"
        or document.get("runtimeStatus") != "NOT_PROVIDED"
        or not isinstance(counts, dict)
        or set(counts) != set(_PASS_COUNTS)
        or any(type(counts[key]) is not int for key in _PASS_COUNTS)
        or counts != _PASS_COUNTS
    ):
        raise SupervisorError("INVALID_PASS")
    checksum = document.get("manifestChecksum")
    if not isinstance(checksum, str) or _CHECKSUM.fullmatch(checksum) is None:
        raise SupervisorError("INVALID_PASS")
    path_text = document.get("manifestPath")
    if not isinstance(path_text, str) or len(path_text.encode()) > 512:
        raise SupervisorError("INVALID_PASS")
    path = PurePosixPath(path_text)
    parts = path.parts
    if (
        path.is_absolute()
        or len(parts) != 5
        or parts[0:2] != ("data", "acceptance")
        or parts[3] != "java-spring"
        or any(_SAFE_PART.fullmatch(part) is None for part in parts)
        or parts[4] != f"sha256-{checksum.removeprefix('sha256:')}.json"
    ):
        raise SupervisorError("INVALID_PASS")
    return document


def _validate_child_failure_document(content: bytes) -> dict[str, object]:
    try:
        document = _one_json_object(content)
    except SupervisorError as error:
        raise SupervisorError("INVALID_CHILD_FAILURE") from error
    evidence_class = document.get("evidenceClass")
    outcome = document.get("outcome")
    reason = document.get("reasonCode")
    required_failure = (
        evidence_class == "LOCAL_REAL_REPOSITORY_REQUIRED"
        and outcome == "INTEGRATION_REQUIRED"
        and isinstance(reason, str)
        and reason in _REQUIRED_FAILURE_REASONS
    )
    actual_failure = (
        evidence_class == "LOCAL_REAL_REPOSITORY_FAIL"
        and outcome == "FAIL"
        and isinstance(reason, str)
        and reason in _FAILURE_REASONS
    )
    if set(document) != _FAILURE_KEYS or not (required_failure or actual_failure):
        raise SupervisorError("INVALID_CHILD_FAILURE")
    return document


def _validate_interpreter(root: Path) -> None:
    expected_prefix = root / "apps" / "api" / ".venv"
    expected_executable = expected_prefix / "bin" / "python"
    if (
        not root.is_absolute()
        or Path(sys.executable) != expected_executable
        or Path(sys.prefix).resolve() != expected_prefix.resolve(strict=True)
        or not sys.flags.isolated
        or not sys.flags.no_user_site
    ):
        raise SupervisorError("RUNNER_ENVIRONMENT_UNAVAILABLE")
    try:
        executable = expected_executable.resolve(strict=True)
        metadata = executable.stat()
        bin_metadata = expected_executable.parent.stat()
    except OSError as error:
        raise SupervisorError("RUNNER_ENVIRONMENT_UNAVAILABLE") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        or not stat.S_ISDIR(bin_metadata.st_mode)
        or bin_metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise SupervisorError("RUNNER_ENVIRONMENT_UNAVAILABLE")


def _emit_document(document: Mapping[str, object]) -> None:
    print(
        json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _emit_failure(code: str) -> None:
    _emit_document(
        {
            "evidenceClass": "LOCAL_REAL_REPOSITORY_REQUIRED",
            "outcome": "INTEGRATION_REQUIRED",
            "reasonCode": code,
        }
    )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        if len(arguments) != 1:
            raise SupervisorError("RUNNER_ENVIRONMENT_UNAVAILABLE")
        root = Path(arguments[0])
        _validate_interpreter(root)
        child_environment = _child_environment(os.environ)
        return_code, stdout, _stderr = _run_bounded_child(
            [
                sys.executable,
                "-I",
                str(root / "tests" / "integration" / "test_spring_petclinic_repository.py"),
            ],
            child_environment,
            timeout_seconds=120.0,
            stdout_limit=4096,
            stderr_limit=4096,
            cwd=root,
        )
        if return_code != 0:
            try:
                child_failure = _validate_child_failure_document(stdout)
            except SupervisorError as error:
                raise SupervisorError("RUNNER_ENVIRONMENT_UNAVAILABLE") from error
            _emit_document(child_failure)
            return 2
        document = _validate_pass_document(stdout)
        print(json.dumps(document, sort_keys=True, separators=(",", ":")))
        return 0
    except SupervisorError as error:
        _emit_failure(error.code)
        return 2
    except Exception:
        _emit_failure("RUNNER_ENVIRONMENT_UNAVAILABLE")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
