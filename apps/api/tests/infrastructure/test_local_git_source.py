from __future__ import annotations

import os
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from lineage_api.application.repository_sources import (
    RepositoryCheckoutDescriptor,
    RepositorySourceError,
    RepositorySourceLimits,
)
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


def test_snapshot_rejects_dirty_tracked_content_but_not_untracked_files(tmp_path: Path) -> None:
    root, revision = _repository(tmp_path)
    (root / "README.md").write_text("changed\n", encoding="utf-8")

    with pytest.raises(RepositorySourceError, match="tracked content is not clean"):
        _source().snapshot(_descriptor(root, revision))


def test_cleanliness_check_never_executes_repository_configured_filters(tmp_path: Path) -> None:
    root, revision = _repository(
        tmp_path,
        files={
            ".gitattributes": b"README.md filter=hostile\n",
            "README.md": b"original\n",
        },
    )
    marker = tmp_path / "filter-ran"
    _git(root, "config", "filter.hostile.clean", f"touch '{marker}'; cat")
    (root / "README.md").write_text("mutated!\n", encoding="utf-8")

    with pytest.raises(RepositorySourceError, match="tracked content is not clean"):
        _source().snapshot(_descriptor(root, revision))

    assert not marker.exists()


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


def test_snapshot_read_revalidates_content_after_snapshot(tmp_path: Path) -> None:
    root, revision = _repository(tmp_path)
    snapshot = _source().snapshot(_descriptor(root, revision))
    (root / "README.md").write_bytes(b"same-size")

    with pytest.raises(RepositorySourceError, match="changed after snapshot"):
        snapshot.read_bytes("README.md")


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
