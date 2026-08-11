from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from lineage_api.application.repository_sources import (
    RemoteRepositoryRequest,
    RepositorySnapshot,
    RepositorySourceLimits,
)
from lineage_api.infrastructure import remote_git_source
from lineage_api.infrastructure.remote_git_source import (
    BoundedProcessGroupRunner,
    HttpsGitTransport,
    RemoteFetchPlan,
    RemoteGitRepositorySource,
    RemoteGitSourceError,
)


ORIGIN = "https://example.com/acme/demo"
PUBLIC_ADDRESS = "93.184.216.34"


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        shell=False,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _remote_repository(
    tmp_path: Path,
    *,
    files: dict[str, bytes] | None = None,
    name: str = "remote",
) -> tuple[Path, str]:
    root = tmp_path / name
    root.mkdir()
    _git(root, "init", "--quiet")
    _git(root, "config", "user.email", "lineage@example.com")
    _git(root, "config", "user.name", "Lineage Test")
    for relative_path, content in (files or {"README.md": b"demo\n"}).items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    _git(root, "add", "--all")
    _git(root, "commit", "--quiet", "-m", "fixture")
    return root, _git(root, "rev-parse", "HEAD")


def _request(revision: str, *, origin: str = ORIGIN) -> RemoteRepositoryRequest:
    return RemoteRepositoryRequest(
        origin=origin,
        repository=origin.rsplit("/", 1)[-1],
        revision=revision,
        environment="test",
        platform="local",
        system="petclinic",
        analyzer_pack="java-spring-data-jpa-v1",
        ruleset="java-spring-v1",
    )


def _resolver(*addresses: str):
    answers = [
        (
            socket.AF_INET6 if ":" in address else socket.AF_INET,
            socket.SOCK_STREAM,
            6,
            "",
            (address, 0, 0, 0) if ":" in address else (address, 0),
        )
        for address in addresses
    ]

    def resolver(*args: object, **kwargs: object) -> list[tuple[object, ...]]:
        return answers

    return resolver


def _fixture_transport(remote: Path, revision: str, *, origin: str = ORIGIN):
    """A legitimate injected transport: a real Git fetch over a local boundary."""

    plans: list[RemoteFetchPlan] = []

    def transport(plan: RemoteFetchPlan) -> None:
        plans.append(plan)
        subprocess.run(
            [
                "git",
                "clone",
                "--quiet",
                "--no-checkout",
                "--no-local",
                str(remote),
                str(plan.checkout_root),
            ],
            shell=False,
            check=True,
            capture_output=True,
        )
        _git(plan.checkout_root, "checkout", "--quiet", "--detach", revision)
        _git(plan.checkout_root, "remote", "set-url", "origin", plan.origin)

    transport.plans = plans  # type: ignore[attr-defined]
    return transport


def _source(
    transport,
    *,
    resolver=None,
    limits: RepositorySourceLimits | None = None,
    timeout_seconds: int = 30,
) -> RemoteGitRepositorySource:
    return RemoteGitRepositorySource(
        limits
        or RepositorySourceLimits(
            max_files=100,
            max_file_bytes=1024 * 1024,
            max_total_bytes=4 * 1024 * 1024,
        ),
        timeout_seconds=timeout_seconds,
        output_limit_bytes=64 * 1024,
        resolver=resolver or _resolver(PUBLIC_ADDRESS),
        transport=transport,
    )


def test_acquires_an_exact_revision_through_an_injected_transport(tmp_path: Path) -> None:
    remote, revision = _remote_repository(
        tmp_path,
        files={"README.md": b"demo\n", "src/app.py": b"print('x')\n"},
    )
    transport = _fixture_transport(remote, revision)

    snapshot = _source(transport).acquire(_request(revision))

    assert isinstance(snapshot, RepositorySnapshot)
    assert snapshot.revision == revision
    assert snapshot.origin == ORIGIN
    assert snapshot.paths == ("README.md", "src/app.py")
    assert snapshot.scope_digest.startswith("sha256:")
    assert snapshot.read_bytes("src/app.py") == b"print('x')\n"


def test_snapshot_survives_guaranteed_checkout_cleanup(tmp_path: Path) -> None:
    remote, revision = _remote_repository(tmp_path, files={"README.md": b"demo\n"})
    transport = _fixture_transport(remote, revision)

    snapshot = _source(transport).acquire(_request(revision))
    checkout_root = transport.plans[0].checkout_root  # type: ignore[attr-defined]

    assert not checkout_root.exists()
    assert snapshot.read_bytes("README.md") == b"demo\n"


def test_repeated_acquisition_reproduces_identical_durable_identity(tmp_path: Path) -> None:
    remote, revision = _remote_repository(
        tmp_path,
        files={"README.md": b"demo\n", "src/app.py": b"print('x')\n"},
    )
    source = _source(_fixture_transport(remote, revision))

    first = source.acquire(_request(revision))
    second = source.acquire(_request(revision))

    assert first.scope_digest == second.scope_digest
    assert first.paths == second.paths
    assert first.read_bytes("README.md") == second.read_bytes("README.md")


def test_rejects_a_checkout_whose_head_is_not_the_requested_revision(tmp_path: Path) -> None:
    remote, revision = _remote_repository(tmp_path)
    (remote / "README.md").write_bytes(b"drift\n")
    _git(remote, "add", "--all")
    _git(remote, "commit", "--quiet", "-m", "drift")
    other = _git(remote, "rev-parse", "HEAD")
    transport = _fixture_transport(remote, other)

    with pytest.raises(RemoteGitSourceError):
        _source(transport).acquire(_request(revision))

    assert not transport.plans[0].checkout_root.exists()  # type: ignore[attr-defined]


def test_rejects_a_checkout_whose_origin_does_not_match_the_request(tmp_path: Path) -> None:
    remote, revision = _remote_repository(tmp_path)
    transport = _fixture_transport(remote, revision, origin="https://example.com/acme/other")

    def rewriting_transport(plan: RemoteFetchPlan) -> None:
        transport(plan)
        _git(plan.checkout_root, "remote", "set-url", "origin", "https://example.com/acme/other")

    with pytest.raises(RemoteGitSourceError):
        _source(rewriting_transport).acquire(_request(revision))


def test_rejects_tracked_symlinks_and_unsupported_entries(tmp_path: Path) -> None:
    root = tmp_path / "remote"
    root.mkdir()
    _git(root, "init", "--quiet")
    _git(root, "config", "user.email", "lineage@example.com")
    _git(root, "config", "user.name", "Lineage Test")
    (root / "README.md").write_bytes(b"demo\n")
    os.symlink("README.md", root / "link.md")
    _git(root, "add", "--all")
    _git(root, "commit", "--quiet", "-m", "fixture")
    revision = _git(root, "rev-parse", "HEAD")
    transport = _fixture_transport(root, revision)

    with pytest.raises(RemoteGitSourceError):
        _source(transport).acquire(_request(revision))


def test_enforces_bounded_file_counts_and_bytes(tmp_path: Path) -> None:
    remote, revision = _remote_repository(
        tmp_path,
        files={f"file-{index}.txt": b"x" * 64 for index in range(6)},
    )
    transport = _fixture_transport(remote, revision)

    with pytest.raises(RemoteGitSourceError):
        _source(
            transport,
            limits=RepositorySourceLimits(
                max_files=3, max_file_bytes=1024, max_total_bytes=4096
            ),
        ).acquire(_request(revision))


@pytest.mark.parametrize(
    "address",
    ["10.0.0.1", "127.0.0.1", "169.254.1.1", "::1", "fe80::1"],
)
def test_refuses_to_connect_when_resolution_yields_a_private_address(
    address: str, tmp_path: Path
) -> None:
    calls: list[RemoteFetchPlan] = []

    def transport(plan: RemoteFetchPlan) -> None:
        calls.append(plan)

    with pytest.raises(RemoteGitSourceError):
        _source(transport, resolver=_resolver(address)).acquire(_request("a" * 40))

    assert calls == []


def test_resolves_destinations_immediately_before_every_connection(tmp_path: Path) -> None:
    remote, revision = _remote_repository(tmp_path)
    resolutions: list[str] = []

    def resolver(host: str, *args: object, **kwargs: object):
        resolutions.append(host)
        return _resolver(PUBLIC_ADDRESS)(host)

    source = _source(_fixture_transport(remote, revision), resolver=resolver)
    source.acquire(_request(revision))
    source.acquire(_request(revision))

    assert resolutions == ["example.com", "example.com"]


def test_pins_resolved_addresses_into_the_fetch_plan(tmp_path: Path) -> None:
    remote, revision = _remote_repository(tmp_path)
    transport = _fixture_transport(remote, revision)

    _source(transport, resolver=_resolver(PUBLIC_ADDRESS, "8.8.8.8")).acquire(
        _request(revision)
    )
    plan = transport.plans[0]  # type: ignore[attr-defined]

    assert plan.addresses == ("8.8.8.8", PUBLIC_ADDRESS)
    assert plan.host == "example.com"
    assert plan.port == 443
    assert plan.revision == revision
    assert plan.checkout_root.is_absolute()


def test_removes_the_private_checkout_on_every_failure_path(tmp_path: Path) -> None:
    seen: list[Path] = []

    def failing_transport(plan: RemoteFetchPlan) -> None:
        seen.append(plan.checkout_root)
        assert plan.checkout_root.exists()
        assert plan.checkout_root.stat().st_mode & 0o077 == 0
        raise RuntimeError("transport exploded with /secret/path and token abc123")

    with pytest.raises(RemoteGitSourceError) as caught:
        _source(failing_transport).acquire(_request("b" * 40))

    assert seen and not seen[0].exists()
    assert "/secret/path" not in str(caught.value)
    assert "abc123" not in str(caught.value)
    assert len(str(caught.value)) <= 160


@pytest.mark.parametrize(
    "origin",
    [
        "https://user:token@example.com/acme/demo",
        "http://example.com/acme/demo",
        "https://example.com/acme/demo?ref=main",
        "https://example.com/",
    ],
)
def test_rejects_unsafe_origins_before_any_network_activity(origin: str) -> None:
    def transport(plan: RemoteFetchPlan) -> None:  # pragma: no cover - must not run
        raise AssertionError("transport must not be reached")

    with pytest.raises((RemoteGitSourceError, ValueError)):
        _source(transport).acquire(_request("c" * 40, origin=origin))


@pytest.mark.parametrize("revision", ["HEAD", "main", "a" * 39, "A" * 40, "z" * 40])
def test_rejects_non_exact_revisions(revision: str) -> None:
    def transport(plan: RemoteFetchPlan) -> None:  # pragma: no cover - must not run
        raise AssertionError("transport must not be reached")

    with pytest.raises((RemoteGitSourceError, ValueError)):
        _source(transport).acquire(_request(revision))


def test_https_transport_argv_disables_redirects_proxies_hooks_and_helpers() -> None:
    recorded: list[list[str]] = []

    class _Runner:
        def run(self, argv, *, env, stdout_limit, stderr_limit, timeout_seconds):
            recorded.append(list(argv))
            return b""

    transport = HttpsGitTransport(runner=_Runner())
    plan = RemoteFetchPlan(
        origin=ORIGIN,
        revision="d" * 40,
        host="example.com",
        port=443,
        addresses=(PUBLIC_ADDRESS,),
        checkout_root=Path("/tmp/does-not-matter"),
        timeout_seconds=5.0,
        output_limit_bytes=1024,
    )

    transport(plan)
    flattened = [argument for argv in recorded for argument in argv]

    assert recorded, "the HTTPS transport must invoke Git"
    for option in (
        "http.followRedirects=false",
        "credential.helper=",
        "core.hooksPath=/dev/null",
        f"http.{ORIGIN}.proxy=",
        "http.proxy=",
        "protocol.allow=never",
        "protocol.https.allow=always",
        "http.sslVerify=true",
        "fetch.recurseSubmodules=no",
        f"http.curloptResolve=example.com:443:{PUBLIC_ADDRESS}",
    ):
        assert option in flattened, f"HTTPS transport is missing {option}"
    assert all("--upload-pack" not in argument for argument in flattened)
    assert any(argument == "d" * 40 for argument in flattened)


def test_https_transport_child_environment_is_a_minimal_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in {
        "HTTP_PROXY": "http://attacker.example",
        "http_proxy": "http://attacker.example",
        "HTTPS_PROXY": "http://attacker.example",
        "ALL_PROXY": "socks5://attacker.example",
        "GIT_ASKPASS": "/tmp/askpass",
        "GIT_SSH_COMMAND": "/tmp/ssh",
        "AWS_SECRET_ACCESS_KEY": "secret",
        "GITHUB_TOKEN": "token",
        "LD_PRELOAD": "/tmp/evil.so",
    }.items():
        monkeypatch.setenv(name, value)

    recorded: list[dict[str, str]] = []

    class _Runner:
        def run(self, argv, *, env, stdout_limit, stderr_limit, timeout_seconds):
            recorded.append(dict(env))
            return b""

    transport = HttpsGitTransport(runner=_Runner())
    transport(
        RemoteFetchPlan(
            origin=ORIGIN,
            revision="e" * 40,
            host="example.com",
            port=443,
            addresses=(PUBLIC_ADDRESS,),
            checkout_root=Path("/tmp/does-not-matter"),
            timeout_seconds=5.0,
            output_limit_bytes=1024,
        )
    )

    assert recorded
    for environment in recorded:
        assert "secret" not in environment.values()
        assert "token" not in environment.values()
        for forbidden in (
            "HTTP_PROXY",
            "http_proxy",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "GIT_ASKPASS",
            "GIT_SSH_COMMAND",
            "AWS_SECRET_ACCESS_KEY",
            "GITHUB_TOKEN",
            "LD_PRELOAD",
        ):
            assert forbidden not in environment
        assert environment["GIT_TERMINAL_PROMPT"] == "0"
        assert environment["GIT_ALLOW_PROTOCOL"] == "https"
        assert environment["GIT_CONFIG_NOSYSTEM"] == "1"


@pytest.mark.parametrize(
    ("stream", "message"),
    [("stdout", "stdout"), ("stderr", "stderr")],
)
def test_group_runner_caps_flooded_output(stream: str, message: str) -> None:
    descriptor = 1 if stream == "stdout" else 2

    with pytest.raises(RemoteGitSourceError, match=message):
        BoundedProcessGroupRunner().run(
            [
                sys.executable,
                "-c",
                f"import os\nwhile True:\n    os.write({descriptor}, b'x' * 65536)\n",
            ],
            env={"PATH": os.defpath},
            stdout_limit=4096,
            stderr_limit=4096,
            timeout_seconds=10,
        )


def test_group_runner_enforces_the_overall_deadline() -> None:
    started = time.monotonic()

    with pytest.raises(RemoteGitSourceError, match="timed out"):
        BoundedProcessGroupRunner().run(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            env={"PATH": os.defpath},
            stdout_limit=1024,
            stderr_limit=1024,
            timeout_seconds=0.25,
        )

    assert time.monotonic() - started < 10


def test_group_runner_kills_sigterm_resistant_descendants(tmp_path: Path) -> None:
    marker = tmp_path / "descendant.log"
    program = (
        "import os, signal, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
        "marker = sys.argv[1]\n"
        "if os.fork() == 0:\n"
        "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "    signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
        "    while True:\n"
        "        open(marker, 'ab').write(b'.')\n"
        "        time.sleep(0.02)\n"
        "while True:\n"
        "    time.sleep(0.02)\n"
    )

    with pytest.raises(RemoteGitSourceError, match="timed out"):
        BoundedProcessGroupRunner().run(
            [sys.executable, "-c", program, str(marker)],
            env={"PATH": os.defpath},
            stdout_limit=1024,
            stderr_limit=1024,
            timeout_seconds=0.4,
        )

    assert marker.exists(), "the descendant never started"
    settled = marker.stat().st_size
    time.sleep(0.4)
    assert marker.stat().st_size == settled, "a descendant survived process-group termination"


def test_group_runner_reports_a_failed_process_without_echoing_output() -> None:
    with pytest.raises(RemoteGitSourceError) as caught:
        BoundedProcessGroupRunner().run(
            [
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('fatal: token ghp_secret leaked'); sys.exit(3)",
            ],
            env={"PATH": os.defpath},
            stdout_limit=1024,
            stderr_limit=1024,
            timeout_seconds=5,
        )

    assert "ghp_secret" not in str(caught.value)
    assert len(str(caught.value)) <= 160


def test_module_exposes_the_public_remote_source_contract() -> None:
    assert hasattr(remote_git_source, "RemoteGitRepositorySource")
    assert hasattr(remote_git_source, "HttpsGitTransport")
    assert hasattr(remote_git_source, "BoundedProcessGroupRunner")
    assert callable(remote_git_source.resolve_public_git_addresses)
