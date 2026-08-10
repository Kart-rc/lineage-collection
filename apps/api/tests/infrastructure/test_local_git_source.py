from __future__ import annotations

import inspect
import multiprocessing
import os
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from lineage_api.application.repository_sources import (
    RepositoryCheckoutDescriptor,
    RepositorySnapshot,
    RepositorySourceError,
    RepositorySourceLimits,
)
from lineage_api.infrastructure import local_git_source
from lineage_api.infrastructure.local_git_source import LocalGitRepositorySource


ORIGIN = "https://example.com/acme/demo"


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        shell=False,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _repository(
    tmp_path: Path,
    *,
    files: dict[str, bytes] | None = None,
    directory_name: str = "checkout",
    origin: str = ORIGIN,
) -> tuple[Path, str]:
    root = tmp_path / directory_name
    root.mkdir()
    _git(root, "init", "--quiet")
    _git(root, "config", "user.email", "lineage@example.com")
    _git(root, "config", "user.name", "Lineage Test")
    _git(root, "remote", "add", "origin", origin)
    for relative_path, content in (files or {"README.md": b"demo\n"}).items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    _git(root, "add", "--all")
    _git(root, "commit", "--quiet", "-m", "fixture")
    return root, _git(root, "rev-parse", "HEAD")


def _descriptor(
    root: Path,
    revision: str,
    *,
    origin: str = ORIGIN,
) -> RepositoryCheckoutDescriptor:
    return RepositoryCheckoutDescriptor(
        origin=origin,
        repository="demo",
        revision=revision,
        checkout_root=root,
        environment="test",
        platform="local",
        system="petclinic",
        analyzer_pack="java-spring-data-jpa-v1",
        ruleset="java-spring-v1",
    )


def _source(**overrides: int) -> LocalGitRepositorySource:
    values = {
        "max_files": 100,
        "max_file_bytes": 1024,
        "max_total_bytes": 4096,
    }
    values.update(overrides)
    return LocalGitRepositorySource(RepositorySourceLimits(**values))


def _read_snapshot_file_in_child(snapshot: RepositorySnapshot, relative_path: str) -> None:
    try:
        snapshot.read_bytes(relative_path)
    except RepositorySourceError as error:
        if "regular file" in str(error):
            return
        raise SystemExit(2) from error
    raise SystemExit(3)


def test_snapshot_pins_descriptor_and_sorted_tracked_scope_without_running_code(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "repository-code-ran"
    script = f"#!/bin/sh\ntouch {marker}\n".encode()
    root, revision = _repository(
        tmp_path,
        directory_name="checkout;touch-command-injection",
        origin="https://EXAMPLE.com/acme/demo.git/",
        files={"z-last.txt": b"z", "a-first.txt": b"alpha", "gradlew": script},
    )
    (root / "gradlew").chmod(0o755)
    _git(root, "add", "gradlew")
    _git(root, "commit", "--quiet", "-m", "mark script executable")
    revision = _git(root, "rev-parse", "HEAD")
    (root / "untracked-secret.txt").write_text("not in scope", encoding="utf-8")
    descriptor = _descriptor(root, revision)

    first = _source().snapshot(descriptor)
    second = _source().snapshot(descriptor)

    assert first.descriptor == descriptor
    assert first.schema_version == "1.0.0"
    assert first.origin == descriptor.origin
    assert first.repository == descriptor.repository
    assert first.revision == descriptor.revision
    assert first.checkout_root == root.resolve()
    assert first.environment == descriptor.environment
    assert first.platform == descriptor.platform
    assert first.system == descriptor.system
    assert first.analyzer_pack == descriptor.analyzer_pack
    assert first.ruleset == descriptor.ruleset
    assert first.paths == ("a-first.txt", "gradlew", "z-last.txt")
    assert first.scope_digest == second.scope_digest
    assert first.scope_digest.startswith("sha256:")
    assert len(first.scope_digest) == len("sha256:") + 64
    assert first.read_bytes("a-first.txt") == b"alpha"
    assert "untracked-secret.txt" not in first.paths
    assert not marker.exists()
    with pytest.raises(FrozenInstanceError):
        first.scope_digest = "sha256:" + "0" * 64  # type: ignore[misc]


def test_scope_digest_is_bound_to_path_size_and_content(tmp_path: Path) -> None:
    root, revision = _repository(tmp_path, files={"source.txt": b"first"})
    first = _source().snapshot(_descriptor(root, revision))

    (root / "source.txt").write_bytes(b"other")
    _git(root, "add", "source.txt")
    _git(root, "commit", "--quiet", "-m", "change content")
    second_revision = _git(root, "rev-parse", "HEAD")
    second = _source().snapshot(_descriptor(root, second_revision))

    assert first.scope_digest != second.scope_digest


@pytest.mark.parametrize(
    ("revision", "origin", "message"),
    [
        ("0" * 40, ORIGIN, "revision"),
        (None, "https://other.example.com/acme/demo", "origin"),
    ],
)
def test_snapshot_rejects_wrong_revision_or_origin(
    tmp_path: Path,
    revision: str | None,
    origin: str,
    message: str,
) -> None:
    root, actual_revision = _repository(tmp_path)

    with pytest.raises(RepositorySourceError, match=message):
        _source().snapshot(_descriptor(root, revision or actual_revision, origin=origin))


def test_snapshot_reads_exact_committed_bytes_instead_of_dirty_worktree_content(
    tmp_path: Path,
) -> None:
    root, revision = _repository(tmp_path)
    clean_snapshot = _source().snapshot(_descriptor(root, revision))
    (root / "README.md").write_text("changed\n", encoding="utf-8")

    dirty_worktree_snapshot = _source().snapshot(_descriptor(root, revision))

    assert dirty_worktree_snapshot.read_bytes("README.md") == b"demo\n"
    assert dirty_worktree_snapshot.scope_digest == clean_snapshot.scope_digest


def test_snapshot_reads_lf_blob_when_attributes_checkout_crlf(tmp_path: Path) -> None:
    committed = b"@echo off\necho canonical\n"
    root, revision = _repository(
        tmp_path,
        files={
            ".gitattributes": b"*.bat text eol=crlf\n",
            "gradlew.bat": committed,
        },
    )
    (root / "gradlew.bat").unlink()
    _git(root, "checkout", "--", "gradlew.bat")
    assert (root / "gradlew.bat").read_bytes() == committed.replace(b"\n", b"\r\n")
    assert _git(root, "status", "--porcelain=v1") == ""

    snapshot = _source().snapshot(_descriptor(root, revision))

    assert snapshot.read_bytes("gradlew.bat") == committed


def test_snapshot_never_executes_repository_configured_filters(tmp_path: Path) -> None:
    root, revision = _repository(
        tmp_path,
        files={
            ".gitattributes": b"README.md filter=hostile\n",
            "README.md": b"original\n",
        },
    )
    clean_marker = tmp_path / "clean-filter-ran"
    smudge_marker = tmp_path / "smudge-filter-ran"
    _git(root, "config", "filter.hostile.clean", f"touch '{clean_marker}'; cat")
    _git(root, "config", "filter.hostile.smudge", f"touch '{smudge_marker}'; cat")
    (root / "README.md").write_text("mutated!\n", encoding="utf-8")

    snapshot = _source().snapshot(_descriptor(root, revision))

    assert snapshot.read_bytes("README.md") == b"original\n"
    assert not clean_marker.exists()
    assert not smudge_marker.exists()


def test_snapshot_reads_each_verified_blob_oid_once_with_a_byte_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, revision = _repository(tmp_path)
    expected_oid = _git(root, "rev-parse", "HEAD:README.md")
    cat_file_calls: list[tuple[str, int]] = []
    tree_revisions: list[str] = []
    original_git = LocalGitRepositorySource._git

    def recording_git(
        source: LocalGitRepositorySource,
        checkout: Path,
        *arguments: str,
        stdout_limit: int,
    ) -> bytes:
        if arguments[:2] == ("cat-file", "blob"):
            cat_file_calls.append((arguments[2], stdout_limit))
        elif arguments[:1] == ("ls-tree",):
            tree_revisions.append(arguments[-1])
        return original_git(source, checkout, *arguments, stdout_limit=stdout_limit)

    monkeypatch.setattr(LocalGitRepositorySource, "_git", recording_git)

    snapshot = _source().snapshot(_descriptor(root, revision))

    assert snapshot.read_bytes("README.md") == b"demo\n"
    assert cat_file_calls == [(expected_oid, 1025)]
    assert tree_revisions == [revision, revision]


def test_snapshot_rejects_blob_bytes_that_do_not_match_the_verified_oid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, revision = _repository(tmp_path)
    original_git = LocalGitRepositorySource._git
    secret = b"forged-secret-blob-content"

    def corrupt_blob(
        source: LocalGitRepositorySource,
        checkout: Path,
        *arguments: str,
        stdout_limit: int,
    ) -> bytes:
        if arguments[:2] == ("cat-file", "blob"):
            return secret
        return original_git(source, checkout, *arguments, stdout_limit=stdout_limit)

    monkeypatch.setattr(LocalGitRepositorySource, "_git", corrupt_blob)

    with pytest.raises(RepositorySourceError, match="verified object ID") as captured:
        _source().snapshot(_descriptor(root, revision))

    assert secret.decode("ascii") not in str(captured.value)


def test_snapshot_rejects_head_and_index_aba_during_blob_acquisition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, revision_a = _repository(tmp_path, files={"README.md": b"revision-a\n"})
    (root / "README.md").write_bytes(b"revision-b\n")
    _git(root, "add", "README.md")
    _git(root, "commit", "--quiet", "-m", "revision B")
    revision_b = _git(root, "rev-parse", "HEAD")
    _git(root, "reset", "--hard", "--quiet", revision_a)
    original_git = LocalGitRepositorySource._git
    switched_to_b = False
    state_checks = 0

    def aba_git(
        source: LocalGitRepositorySource,
        checkout: Path,
        *arguments: str,
        stdout_limit: int,
    ) -> bytes:
        nonlocal state_checks, switched_to_b
        if arguments[:2] == ("rev-parse", "--is-inside-work-tree"):
            state_checks += 1
            if state_checks == 2:
                _git(checkout, "reset", "--hard", "--quiet", revision_a)
        elif arguments[:1] == ("ls-files",) and not switched_to_b:
            _git(checkout, "reset", "--hard", "--quiet", revision_b)
            switched_to_b = True
        return original_git(source, checkout, *arguments, stdout_limit=stdout_limit)

    monkeypatch.setattr(LocalGitRepositorySource, "_git", aba_git)

    try:
        with pytest.raises(RepositorySourceError):
            _source().snapshot(_descriptor(root, revision_a))
    finally:
        _git(root, "reset", "--hard", "--quiet", revision_a)

    assert switched_to_b


@pytest.mark.parametrize("drift", ["content", "path", "mode"])
def test_snapshot_rejects_staged_index_drift_from_requested_revision_tree(
    tmp_path: Path,
    drift: str,
) -> None:
    root, revision = _repository(tmp_path)
    if drift == "content":
        (root / "README.md").write_bytes(b"staged-change\n")
        _git(root, "add", "README.md")
    elif drift == "path":
        _git(root, "mv", "README.md", "RENAMED.md")
    else:
        _git(root, "update-index", "--chmod=+x", "README.md")

    with pytest.raises(RepositorySourceError):
        _source().snapshot(_descriptor(root, revision))


def test_git_commands_ignore_caller_global_and_system_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, revision = _repository(tmp_path)
    hostile_global = tmp_path / "hostile-global.gitconfig"
    hostile_system = tmp_path / "hostile-system.gitconfig"
    hostile_global.write_text("[invalid\n", encoding="utf-8")
    hostile_system.write_text("[also-invalid\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(hostile_global))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(hostile_system))

    snapshot = _source().snapshot(_descriptor(root, revision))

    assert snapshot.read_bytes("README.md") == b"demo\n"


def test_snapshot_never_resolves_git_from_a_checkout_controlled_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, revision = _repository(tmp_path)
    marker = tmp_path / "fake-git-ran"
    fake_binary_directory = root / "checkout-tools"
    fake_binary_directory.mkdir()
    fake_git = fake_binary_directory / "git"
    fake_git.write_text(
        f"#!/bin/sh\ntouch '{marker}'\nexit 99\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    monkeypatch.setenv(
        "PATH",
        os.pathsep.join((str(fake_binary_directory), os.environ.get("PATH", ""))),
    )

    snapshot = _source().snapshot(_descriptor(root, revision))

    assert snapshot.read_bytes("README.md") == b"demo\n"
    assert not marker.exists()


def test_source_does_not_expose_custom_git_executable_injection() -> None:
    assert "git_executable" not in inspect.signature(LocalGitRepositorySource).parameters


def test_case_alias_cannot_execute_an_injected_checkout_git(
    tmp_path: Path,
) -> None:
    root, revision = _repository(tmp_path)
    marker = tmp_path / "case-aliased-git-ran"
    fake_git = root / "git"
    fake_git.write_text(
        f"#!/bin/sh\ntouch '{marker}'\nexit 99\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    root_text = str(root)
    if not root_text.startswith("/private/"):
        pytest.skip("case-insensitive /PRIVATE alias is specific to the APFS test host")
    aliased_git = Path("/PRIVATE/") / fake_git.relative_to("/private")
    if not aliased_git.exists():
        pytest.skip("the filesystem does not expose the reproduced case alias")

    try:
        source = LocalGitRepositorySource(
            RepositorySourceLimits(
                max_files=100,
                max_file_bytes=1024,
                max_total_bytes=4096,
            ),
            git_executable=aliased_git,
        )
    except (TypeError, ValueError):
        pass
    else:
        with pytest.raises(RepositorySourceError):
            source.snapshot(_descriptor(root, revision))

    assert not marker.exists()


@pytest.mark.parametrize(
    ("stream", "message"),
    [("stdout", "stdout byte limit"), ("stderr", "stderr byte limit")],
)
def test_process_runner_terminates_oversized_git_output(
    stream: str,
    message: str,
) -> None:
    runner_type = getattr(local_git_source, "_BoundedProcessRunner", None)
    assert runner_type is not None, "bounded binary process runner is required"
    descriptor = 1 if stream == "stdout" else 2
    runner = runner_type()

    with pytest.raises(RepositorySourceError, match=message):
        runner.run(
            [sys.executable, "-c", f"import os; os.write({descriptor}, b'x' * 4096)"],
            env={"PATH": os.defpath},
            stdout_limit=128,
            stderr_limit=128,
            timeout_seconds=2,
        )


def test_process_runner_terminates_a_timed_out_git_process() -> None:
    runner_type = getattr(local_git_source, "_BoundedProcessRunner", None)
    assert runner_type is not None, "bounded binary process runner is required"

    with pytest.raises(RepositorySourceError, match="timed out"):
        runner_type().run(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            env={"PATH": os.defpath},
            stdout_limit=128,
            stderr_limit=128,
            timeout_seconds=0.05,
        )


def test_snapshot_rejects_a_tracked_path_above_the_parser_bound(tmp_path: Path) -> None:
    relative_path = "/".join(("a" * 200, "b" * 200, "c" * 200, "value.txt"))
    root, revision = _repository(tmp_path, files={relative_path: b"value"})

    with pytest.raises(RepositorySourceError, match="tracked path byte limit"):
        _source().snapshot(_descriptor(root, revision))


def test_git_child_environment_is_a_minimal_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "AWS_SECRET_ACCESS_KEY",
        "DYLD_INSERT_LIBRARIES",
        "LD_PRELOAD",
        "SSH_AUTH_SOCK",
        "UNRELATED_CALLER_VALUE",
    ):
        monkeypatch.setenv(name, "must-not-cross-boundary")

    environment = local_git_source._git_environment()

    assert set(environment) == {
        "GIT_ATTR_NOSYSTEM",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_SYSTEM",
        "GIT_NO_LAZY_FETCH",
        "GIT_NO_REPLACE_OBJECTS",
        "GIT_OPTIONAL_LOCKS",
        "GIT_PAGER",
        "GIT_TERMINAL_PROMPT",
        "HOME",
        "LANG",
        "LC_ALL",
        "PATH",
        "XDG_CONFIG_HOME",
    }
    assert environment["PATH"] == os.defpath
    assert environment["GIT_NO_LAZY_FETCH"] == "1"
    assert environment["HOME"] == "/var/empty"
    assert environment["XDG_CONFIG_HOME"] == "/var/empty"


def test_snapshot_requires_owner_execute_bit_for_git_executable_mode(tmp_path: Path) -> None:
    root, _ = _repository(tmp_path, files={"tool": b"trusted"})
    (root / "tool").chmod(0o755)
    _git(root, "add", "tool")
    _git(root, "commit", "--quiet", "-m", "mark tool executable")
    revision = _git(root, "rev-parse", "HEAD")
    (root / "tool").chmod(0o641)

    with pytest.raises(RepositorySourceError, match="tracked content is not clean"):
        _source().snapshot(_descriptor(root, revision))


@pytest.mark.parametrize("missing_flag", ["O_NOFOLLOW", "O_DIRECTORY"])
def test_source_fails_closed_without_required_posix_open_flags(
    monkeypatch: pytest.MonkeyPatch,
    missing_flag: str,
) -> None:
    monkeypatch.delattr(os, missing_flag)

    with pytest.raises(RuntimeError, match="POSIX Darwin/Linux"):
        _source()


def test_snapshot_rejects_credential_bearing_remote_without_leaking_credentials(
    tmp_path: Path,
) -> None:
    root, revision = _repository(tmp_path)
    _git(
        root,
        "remote",
        "set-url",
        "origin",
        "https://lineage-user:super-secret@example.com/acme/demo.git",
    )

    with pytest.raises(RepositorySourceError) as captured:
        _source().snapshot(_descriptor(root, revision))

    assert "super-secret" not in str(captured.value)
    assert "lineage-user" not in str(captured.value)


def test_snapshot_rejects_multiple_raw_origin_urls(tmp_path: Path) -> None:
    root, revision = _repository(tmp_path)
    _git(root, "config", "--add", "remote.origin.url", "https://mirror.example/acme/demo")

    with pytest.raises(RepositorySourceError, match="exactly one raw origin URL"):
        _source().snapshot(_descriptor(root, revision))


def test_snapshot_rejects_local_instead_of_origin_rewrite(tmp_path: Path) -> None:
    root, revision = _repository(tmp_path)
    _git(
        root,
        "config",
        "url.https://mirror.example/.insteadOf",
        "https://example.com/",
    )

    with pytest.raises(RepositorySourceError, match="effective origin"):
        _source().snapshot(_descriptor(root, revision))


def test_descriptor_rejects_credentials_and_noncanonical_or_inexact_values(
    tmp_path: Path,
) -> None:
    root = tmp_path.resolve()
    values = {
        "origin": ORIGIN,
        "repository": "demo",
        "revision": "a" * 40,
        "checkout_root": root,
        "environment": "test",
        "platform": "local",
        "system": "petclinic",
        "analyzer_pack": "java-spring-data-jpa-v1",
        "ruleset": "java-spring-v1",
    }

    for field, invalid in (
        ("origin", "https://user:secret@example.com/acme/demo"),
        ("origin", "http://example.com/acme/demo"),
        ("origin", "https://EXAMPLE.com/acme/demo.git"),
        ("origin", "https://example.com:0/acme/demo"),
        ("revision", "A" * 40),
        ("revision", "a" * 39),
        ("checkout_root", Path("relative/checkout")),
    ):
        with pytest.raises(ValueError) as captured:
            RepositoryCheckoutDescriptor(**{**values, field: invalid})  # type: ignore[arg-type]
        assert "secret" not in str(captured.value)

    sha256_descriptor = RepositoryCheckoutDescriptor(
        **{**values, "revision": "b" * 64}  # type: ignore[arg-type]
    )
    assert sha256_descriptor.revision == "b" * 64


def test_snapshot_rejects_tracked_symlink(tmp_path: Path) -> None:
    root, revision = _repository(tmp_path)
    (root / "target.txt").write_text("target", encoding="utf-8")
    os.symlink("target.txt", root / "linked.txt")
    _git(root, "add", "target.txt", "linked.txt")
    _git(root, "commit", "--quiet", "-m", "add symlink")
    revision = _git(root, "rev-parse", "HEAD")

    with pytest.raises(RepositorySourceError, match="symlink"):
        _source().snapshot(_descriptor(root, revision))


def test_snapshot_rejects_tracked_submodule(tmp_path: Path) -> None:
    child, _ = _repository(tmp_path, directory_name="child")
    root, _ = _repository(tmp_path, directory_name="parent")
    _git(
        root,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "--quiet",
        str(child),
        "vendor/child",
    )
    _git(root, "commit", "--quiet", "-am", "add submodule")
    revision = _git(root, "rev-parse", "HEAD")

    with pytest.raises(RepositorySourceError, match="submodule"):
        _source().snapshot(_descriptor(root, revision))


@pytest.mark.parametrize("relative_path", ["../outside.txt", "/etc/passwd", "a/../../escape"])
def test_snapshot_read_rejects_paths_outside_explicit_tracked_scope(
    tmp_path: Path, relative_path: str
) -> None:
    root, revision = _repository(tmp_path)
    snapshot = _source().snapshot(_descriptor(root, revision))

    with pytest.raises(RepositorySourceError, match="relative tracked path"):
        snapshot.read_bytes(relative_path)


def test_snapshot_read_remains_bound_to_committed_bytes_after_worktree_change(
    tmp_path: Path,
) -> None:
    root, revision = _repository(tmp_path)
    snapshot = _source().snapshot(_descriptor(root, revision))
    (root / "README.md").write_bytes(b"same-size")

    assert snapshot.read_bytes("README.md") == b"demo\n"


def test_snapshot_read_rejects_fifo_replacement_without_blocking(tmp_path: Path) -> None:
    root, revision = _repository(tmp_path)
    snapshot = _source().snapshot(_descriptor(root, revision))
    (root / "README.md").unlink()
    os.mkfifo(root / "README.md")
    process = multiprocessing.get_context("fork").Process(
        target=_read_snapshot_file_in_child,
        args=(snapshot, "README.md"),
    )

    process.start()
    process.join(timeout=1.0)
    if process.is_alive():
        process.terminate()
        process.join(timeout=1.0)
        pytest.fail("reading a tracked path replaced by a FIFO blocked")

    assert process.exitcode == 0


def test_snapshot_read_does_not_follow_a_replaced_parent_directory(tmp_path: Path) -> None:
    root, revision = _repository(tmp_path, files={"src/value.txt": b"trusted"})
    snapshot = _source().snapshot(_descriptor(root, revision))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "value.txt").write_bytes(b"trusted")
    (root / "src").rename(root / "original-src")
    os.symlink(outside, root / "src")

    with pytest.raises(RepositorySourceError, match="stable regular file"):
        snapshot.read_bytes("src/value.txt")


@pytest.mark.parametrize(
    ("limits", "files", "message"),
    [
        ({"max_files": 1}, {"a": b"a", "b": b"b"}, "file count"),
        ({"max_file_bytes": 3}, {"large": b"1234"}, "per-file byte"),
        ({"max_total_bytes": 5}, {"a": b"123", "b": b"456"}, "total byte"),
    ],
)
def test_snapshot_enforces_resource_bounds(
    tmp_path: Path,
    limits: dict[str, int],
    files: dict[str, bytes],
    message: str,
) -> None:
    root, revision = _repository(tmp_path, files=files)

    with pytest.raises(RepositorySourceError, match=message):
        _source(**limits).snapshot(_descriptor(root, revision))
