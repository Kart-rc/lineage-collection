from __future__ import annotations

import ipaddress
import os
import selectors
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from lineage_api.application.repository_sources import (
    RemoteRepositoryRequest,
    RepositorySnapshot,
    RepositorySourceLimits,
    canonicalize_https_origin,
)
from lineage_api.infrastructure.local_git_source import (
    LocalGitRepositorySource,
    git_environment,
    trusted_system_git,
)


_MAX_RESOLVED_ADDRESSES = 16
_RESOLUTION_REJECTED = "remote Git host resolution rejected"
_ACQUISITION_REJECTED = "remote Git acquisition rejected"
_MAX_ERROR_BYTES = 160
_GROUP_TERMINATION_PROBES = 40
_GROUP_TERMINATION_INTERVAL_SECONDS = 0.025

# Git configuration that must hold for every remote invocation. Each entry closes a
# specific escape hatch: ambient proxies, redirects, credential helpers, hooks and
# templates, non-HTTPS protocols, submodule recursion, and background maintenance.
_SAFE_GIT_CONFIG = (
    "core.askPass=",
    "core.fsmonitor=false",
    "core.hooksPath=/dev/null",
    "core.pager=cat",
    "credential.helper=",
    "diff.external=",
    "fetch.fsckObjects=true",
    "fetch.recurseSubmodules=no",
    "gc.auto=0",
    "http.followRedirects=false",
    "http.proxy=",
    "http.sslVerify=true",
    "maintenance.auto=false",
    "protocol.allow=never",
    "protocol.file.allow=never",
    "protocol.https.allow=always",
    "submodule.recurse=false",
    "transfer.fsckObjects=true",
)


class RemoteGitSourceError(RuntimeError):
    """Raised when a remote Git source violates source policy or its bounds."""

    def __init__(self, message: str = _ACQUISITION_REJECTED) -> None:
        super().__init__(message[:_MAX_ERROR_BYTES])


def resolve_public_git_addresses(
    host: str,
    *,
    resolver: Callable[..., object] = socket.getaddrinfo,
) -> tuple[str, ...]:
    """Resolve a host to a bounded, deterministic set of public IP addresses."""
    try:
        answers = list(
            resolver(
                host,
                None,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            )
        )
        if not answers or len(answers) > _MAX_RESOLVED_ADDRESSES:
            raise ValueError

        addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
        for answer in answers:
            family, _, _, _, sockaddr = answer
            address = ipaddress.ip_address(sockaddr[0])
            if family not in (socket.AF_INET, socket.AF_INET6):
                raise ValueError
            if address.version != (4 if family == socket.AF_INET else 6):
                raise ValueError
            if (
                not address.is_global
                or address.is_private
                or address.is_loopback
                or address.is_link_local
                or address.is_multicast
                or address.is_reserved
                or address.is_unspecified
            ):
                raise ValueError
            addresses.add(address)
    except Exception:
        raise RemoteGitSourceError(_RESOLUTION_REJECTED) from None

    return tuple(
        str(address)
        for address in sorted(addresses, key=lambda item: (item.version, int(item)))
    )


@dataclass(frozen=True, slots=True)
class RemoteFetchPlan:
    """Everything a transport may know, resolved and validated immediately before use."""

    origin: str
    revision: str
    host: str
    port: int
    addresses: tuple[str, ...]
    checkout_root: Path
    timeout_seconds: float
    output_limit_bytes: int


GitTransport = Callable[[RemoteFetchPlan], None]


class BoundedProcessGroupRunner:
    """Runs one process in its own group under closed output, time, and lifetime bounds."""

    def run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str],
        stdout_limit: int,
        stderr_limit: int,
        timeout_seconds: float,
    ) -> bytes:
        if stdout_limit < 0 or stderr_limit < 0 or timeout_seconds <= 0:
            raise RemoteGitSourceError("remote Git process bounds must be positive")

        process: subprocess.Popen[bytes] | None = None
        selector = selectors.DefaultSelector()
        stdout = bytearray()
        stderr = bytearray()
        buffers = {"stdout": stdout, "stderr": stderr}
        limits = {"stdout": stdout_limit, "stderr": stderr_limit}
        try:
            try:
                process = subprocess.Popen(
                    list(argv),
                    shell=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=dict(env),
                    close_fds=True,
                    start_new_session=True,
                )
            except (OSError, subprocess.SubprocessError):
                raise RemoteGitSourceError("remote Git process could not start") from None
            if process.stdout is None or process.stderr is None:
                raise RemoteGitSourceError("remote Git pipes were not created")
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            deadline = time.monotonic() + timeout_seconds

            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RemoteGitSourceError("remote Git process timed out")
                events = selector.select(remaining)
                if not events:
                    raise RemoteGitSourceError("remote Git process timed out")
                for key, _mask in events:
                    stream_name = str(key.data)
                    buffer = buffers[stream_name]
                    limit = limits[stream_name]
                    chunk = os.read(key.fd, min(64 * 1024, limit - len(buffer) + 1))
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    buffer.extend(chunk)
                    if len(buffer) > limit:
                        raise RemoteGitSourceError(
                            f"remote Git {stream_name} byte limit exceeded"
                        )

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RemoteGitSourceError("remote Git process timed out")
            try:
                return_code = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                raise RemoteGitSourceError("remote Git process timed out") from None
            if return_code != 0:
                raise RemoteGitSourceError("remote Git command failed")
            return bytes(stdout)
        except RemoteGitSourceError:
            if process is not None:
                terminate_process_group(process)
            raise
        except OSError:
            if process is not None:
                terminate_process_group(process)
            raise RemoteGitSourceError("remote Git command failed") from None
        finally:
            selector.close()
            if process is not None:
                if process.stdout is not None:
                    process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()
                terminate_process_group(process)


def terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    """Terminate the complete process group: TERM, bounded probing, then KILL."""
    try:
        group = os.getpgid(process.pid)
    except (OSError, ProcessLookupError):
        return
    if group == os.getpgrp():  # pragma: no cover - defensive; never kill our own group
        return

    for escalation in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(group, escalation)
        except ProcessLookupError:
            break
        except OSError:  # pragma: no cover - permission loss is not recoverable here
            break
        for _ in range(_GROUP_TERMINATION_PROBES):
            if process.poll() is None:
                try:
                    process.wait(timeout=_GROUP_TERMINATION_INTERVAL_SECONDS)
                except subprocess.TimeoutExpired:
                    continue
            try:
                os.killpg(group, 0)
            except ProcessLookupError:
                return
            except OSError:  # pragma: no cover - defensive
                return
            time.sleep(_GROUP_TERMINATION_INTERVAL_SECONDS)

    if process.poll() is None:  # pragma: no cover - already killed above
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass


class HttpsGitTransport:
    """Fetches exactly one commit over HTTPS with pinned addresses and no ambient trust."""

    def __init__(
        self,
        *,
        git_executable: Path | None = None,
        runner: object | None = None,
    ) -> None:
        self._runner = runner if runner is not None else BoundedProcessGroupRunner()
        self._git_executable = (
            Path(git_executable) if git_executable is not None else trusted_system_git().path
        )

    def __call__(self, plan: RemoteFetchPlan) -> None:
        deadline = time.monotonic() + plan.timeout_seconds
        resolve = ",".join(plan.addresses)
        pinned = (
            *_SAFE_GIT_CONFIG,
            f"http.{plan.origin}.proxy=",
            f"http.curloptResolve={plan.host}:{plan.port}:{resolve}",
        )

        self._run(["init", "--quiet", "--template="], plan, pinned, deadline)
        self._run(["remote", "add", "origin", plan.origin], plan, pinned, deadline)
        self._run(
            [
                "fetch",
                "--quiet",
                "--depth",
                "1",
                "--no-tags",
                "--no-recurse-submodules",
                "origin",
                plan.revision,
            ],
            plan,
            pinned,
            deadline,
        )
        self._run(
            ["checkout", "--quiet", "--force", "--detach", plan.revision],
            plan,
            pinned,
            deadline,
        )

    def _run(
        self,
        arguments: Sequence[str],
        plan: RemoteFetchPlan,
        pinned: Sequence[str],
        deadline: float,
    ) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RemoteGitSourceError("remote Git acquisition timed out")
        options: list[str] = ["--no-optional-locks"]
        for entry in pinned:
            options.extend(("-c", entry))
        self._runner.run(
            [
                str(self._git_executable),
                *options,
                "-C",
                str(plan.checkout_root),
                *arguments,
            ],
            env=_remote_git_environment(),
            stdout_limit=plan.output_limit_bytes,
            stderr_limit=plan.output_limit_bytes,
            timeout_seconds=remaining,
        )


def _remote_git_environment() -> dict[str, str]:
    environment = git_environment()
    environment["GIT_ALLOW_PROTOCOL"] = "https"
    environment["GIT_PROTOCOL_FROM_USER"] = "0"
    return environment


@dataclass(frozen=True, slots=True)
class _MaterializedReader:
    contents: dict[str, bytes]

    def __call__(self, relative_path: str) -> bytes:
        content = self.contents.get(relative_path)
        if content is None:
            raise RemoteGitSourceError("source path is outside the explicit tracked scope")
        return content


class RemoteGitRepositorySource:
    """Acquires exact remote Git revisions under bounded source policy."""

    def __init__(
        self,
        limits: RepositorySourceLimits,
        *,
        timeout_seconds: int = 60,
        output_limit_bytes: int = 64 * 1024,
        resolver: Callable[..., object] = socket.getaddrinfo,
        transport: GitTransport | None = None,
    ) -> None:
        if not isinstance(limits, RepositorySourceLimits):
            raise TypeError("bounded repository source limits are required")
        for name, value in (
            ("timeout_seconds", timeout_seconds),
            ("output_limit_bytes", output_limit_bytes),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name.replace('_', ' ')} must be a positive integer")
        self._limits = limits
        self._timeout_seconds = float(timeout_seconds)
        self._output_limit_bytes = output_limit_bytes
        self._resolver = resolver
        self._transport = transport if transport is not None else HttpsGitTransport()

    def acquire(self, request: RemoteRepositoryRequest) -> RepositorySnapshot:
        if not isinstance(request, RemoteRepositoryRequest):
            raise TypeError("a bounded remote repository request is required")
        host, port = self._destination(request.origin)
        # Resolution happens here, immediately before the connection, so a rebound
        # name cannot be validated once and connected to later.
        addresses = resolve_public_git_addresses(host, resolver=self._resolver)

        checkout_root = Path(tempfile.mkdtemp(prefix="lineage-remote-")).resolve(strict=True)
        try:
            os.chmod(checkout_root, 0o700)
            plan = RemoteFetchPlan(
                origin=request.origin,
                revision=request.revision,
                host=host,
                port=port,
                addresses=addresses,
                checkout_root=checkout_root,
                timeout_seconds=self._timeout_seconds,
                output_limit_bytes=self._output_limit_bytes,
            )
            try:
                self._transport(plan)
                snapshot = LocalGitRepositorySource(self._limits).snapshot(
                    request.checkout_descriptor(checkout_root)
                )
                return self._materialize(snapshot)
            except RemoteGitSourceError:
                raise
            except Exception:
                raise RemoteGitSourceError() from None
        finally:
            shutil.rmtree(checkout_root, ignore_errors=True)

    @staticmethod
    def _destination(origin: str) -> tuple[str, int]:
        try:
            if canonicalize_https_origin(origin) != origin:
                raise ValueError
            parsed = urlsplit(origin)
            host = parsed.hostname
            port = parsed.port or 443
        except ValueError:
            raise RemoteGitSourceError("remote Git origin is not a canonical HTTPS URL") from None
        if not host or port < 1 or port > 65535:
            raise RemoteGitSourceError("remote Git origin is not a canonical HTTPS URL")
        return host, port

    @staticmethod
    def _materialize(snapshot: RepositorySnapshot) -> RepositorySnapshot:
        """Detach the verified snapshot from disk so cleanup cannot invalidate it."""
        contents = {path: snapshot.read_bytes(path) for path in snapshot.paths}
        return RepositorySnapshot(
            descriptor=snapshot.descriptor,
            paths=snapshot.paths,
            scope_digest=snapshot.scope_digest,
            _reader=_MaterializedReader(contents),
        )
