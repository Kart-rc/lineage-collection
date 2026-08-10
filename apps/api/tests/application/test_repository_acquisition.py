from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import cast

import pytest

from lineage_api.application.repository_acquisition import (
    GitRepositoryRequest,
    LocalCheckoutRequest,
    RepositoryAcquisitionError,
    RepositoryAcquisitionService,
    RepositorySourceRequest,
)
from lineage_api.application.repository_collection import RepositoryCollectionDescriptor
from lineage_api.application.repository_sources import (
    RepositoryCheckoutDescriptor,
    RepositorySnapshot,
)
from lineage_api.config import Settings


ORIGIN = "https://example.com/acme/spring-service"
REVISION = "a" * 40
_POLICY_ENVIRONMENT = (
    "LINEAGE_ALLOW_LOCAL_REPOSITORY_SOURCES",
    "LINEAGE_REPOSITORY_CLONE_TIMEOUT_SECONDS",
    "LINEAGE_REPOSITORY_GIT_OUTPUT_LIMIT_BYTES",
)


def _request_fields() -> dict[str, str]:
    return {
        "origin": ORIGIN,
        "repository": "spring-service",
        "revision": REVISION,
        "environment": "staging",
        "platform": "postgres",
        "system": "orders",
        "analyzer_pack": "java-spring-data-jpa-v1",
        "ruleset": "spring-data-rules-v1",
        "schema_profile": "postgres",
    }


def _snapshot(request: RepositorySourceRequest, tmp_path: Path) -> RepositorySnapshot:
    content = b"<project />"
    return RepositorySnapshot(
        descriptor=RepositoryCheckoutDescriptor(
            origin=request.origin,
            repository=request.repository,
            revision=request.revision,
            checkout_root=(tmp_path / "materialized").resolve(),
            environment=request.environment,
            platform=request.platform,
            system=request.system,
            analyzer_pack=request.analyzer_pack,
            ruleset=request.ruleset,
        ),
        paths=("pom.xml",),
        scope_digest="sha256:" + "b" * 64,
        _reader=lambda _path: content,
    )


def test_repository_acquisition_policy_service_is_available() -> None:
    assert RepositoryAcquisitionService is not None


def test_source_requests_are_frozen_bounded_and_exact() -> None:
    local = LocalCheckoutRequest(
        **_request_fields(), checkout_root=Path("/private/dev/spring-service")
    )
    remote = GitRepositoryRequest(**_request_fields())

    assert local.source_type == "LOCAL_CHECKOUT"
    assert remote.source_type == "GIT"
    with pytest.raises(FrozenInstanceError):
        remote.repository = "changed"  # type: ignore[misc]

    with pytest.raises(ValueError):
        LocalCheckoutRequest(
            **_request_fields(), checkout_root=Path("relative/spring-service")
        )
    with pytest.raises(ValueError):
        GitRepositoryRequest(**{**_request_fields(), "revision": "b" * 64})
    with pytest.raises(ValueError):
        GitRepositoryRequest(
            **{
                **_request_fields(),
                "origin": "https://user:secret@example.com/acme/spring-service",
            }
        )


@pytest.mark.parametrize(
    "origin",
    (
        "https://localhost/acme/spring-service",
        "https://git.localhost/acme/spring-service",
        "https://intranet/acme/spring-service",
        "https://127.0.0.1/acme/spring-service",
        "https://0.0.0.0/acme/spring-service",
        "https://10.0.0.1/acme/spring-service",
        "https://172.16.0.1/acme/spring-service",
        "https://192.168.0.1/acme/spring-service",
        "https://169.254.1.1/acme/spring-service",
        "https://224.0.0.1/acme/spring-service",
        "https://240.0.0.1/acme/spring-service",
        "https://[::1]/acme/spring-service",
        "https://git.local/acme/spring-service",
        "https://example.com./acme/spring-service",
        "https://example.com:8443/acme/spring-service",
    ),
)
def test_git_requests_reject_non_public_or_ambiguous_origins_without_echoing(
    origin: str,
) -> None:
    with pytest.raises(ValueError) as captured:
        GitRepositoryRequest(**{**_request_fields(), "origin": origin})

    message = str(captured.value)
    assert len(message.encode()) <= 160
    assert origin not in message
    assert message in {
        "origin must be a canonical HTTPS repository URL",
        "GIT repository origin must be a public HTTPS endpoint",
    }


def test_git_requests_accept_canonical_public_https_origins() -> None:
    origin = "https://github.com/acme/spring-service"

    request = GitRepositoryRequest(**{**_request_fields(), "origin": origin})

    assert request.origin == origin


def test_local_checkout_is_rejected_fail_closed_before_provider_or_collection(
    tmp_path: Path,
) -> None:
    request = LocalCheckoutRequest(
        **_request_fields(), checkout_root=(tmp_path / "secret-checkout").resolve()
    )
    service = RepositoryAcquisitionService(
        allow_local_repository_sources=False,
        local_snapshot_provider=lambda _request: pytest.fail("local provider called"),
        remote_snapshot_provider=lambda _request: pytest.fail("remote provider called"),
        collect=lambda _descriptor: pytest.fail("collection called"),
    )

    with pytest.raises(RepositoryAcquisitionError) as captured:
        service.collect(request)

    assert captured.value.code == "LOCAL_SOURCE_DISABLED"
    assert str(captured.value) == "local repository sources are disabled"
    assert str(request.checkout_root) not in str(captured.value)


@pytest.mark.parametrize("source_type", ("LOCAL_CHECKOUT", "GIT"))
def test_enabled_source_mode_uses_only_its_provider_and_approved_collection(
    tmp_path: Path, source_type: str
) -> None:
    request: RepositorySourceRequest
    if source_type == "LOCAL_CHECKOUT":
        request = LocalCheckoutRequest(
            **_request_fields(), checkout_root=(tmp_path / "checkout").resolve()
        )
    else:
        request = GitRepositoryRequest(**_request_fields())
    snapshot = _snapshot(request, tmp_path)
    calls: list[tuple[str, object]] = []

    def local_provider(provided: LocalCheckoutRequest) -> RepositorySnapshot:
        calls.append(("local", provided))
        return snapshot

    def remote_provider(provided: GitRepositoryRequest) -> RepositorySnapshot:
        calls.append(("remote", provided))
        return snapshot

    def collect(descriptor: RepositoryCollectionDescriptor) -> dict[str, object]:
        calls.append(("collect", descriptor))
        assert descriptor.snapshot is snapshot
        assert descriptor.repository == request.repository_identity()
        assert descriptor.analyzer == request.analyzer_identity()
        return {"outcome": "ACCEPTED", "commandId": "command-policy"}

    service = RepositoryAcquisitionService(
        allow_local_repository_sources=True,
        local_snapshot_provider=local_provider,
        remote_snapshot_provider=remote_provider,
        collect=collect,
    )

    result = service.collect(request)

    assert result == {"outcome": "ACCEPTED", "commandId": "command-policy"}
    assert [name for name, _value in calls] == [
        "local" if source_type == "LOCAL_CHECKOUT" else "remote",
        "collect",
    ]


def test_provider_failures_are_bounded_without_path_source_or_raw_output(
    tmp_path: Path,
) -> None:
    request = LocalCheckoutRequest(
        **_request_fields(), checkout_root=(tmp_path / "secret-checkout").resolve()
    )
    marker = f"{request.checkout_root}: return secret_source; fatal: raw git output"

    def fail_provider(_request: LocalCheckoutRequest) -> RepositorySnapshot:
        raise RuntimeError(marker)

    service = RepositoryAcquisitionService(
        allow_local_repository_sources=True,
        local_snapshot_provider=fail_provider,
        remote_snapshot_provider=lambda _request: pytest.fail("remote provider called"),
        collect=lambda _descriptor: pytest.fail("collection called"),
    )

    with pytest.raises(RepositoryAcquisitionError) as captured:
        service.collect(cast(RepositorySourceRequest, request))

    assert captured.value.code == "SOURCE_ACQUISITION_FAILED"
    assert str(captured.value) == "repository source acquisition failed"
    assert str(request.checkout_root) not in str(captured.value)
    assert "secret_source" not in str(captured.value)
    assert "raw git output" not in str(captured.value)


def test_repository_source_settings_default_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in _POLICY_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_environment(project_root=tmp_path)

    assert settings.allow_local_repository_sources is False
    assert settings.repository_clone_timeout_seconds == 60
    assert settings.repository_git_output_limit_bytes == 64 * 1024


def test_repository_source_settings_parse_explicit_bounded_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LINEAGE_ALLOW_LOCAL_REPOSITORY_SOURCES", "true")
    monkeypatch.setenv("LINEAGE_REPOSITORY_CLONE_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("LINEAGE_REPOSITORY_GIT_OUTPUT_LIMIT_BYTES", "131072")

    settings = Settings.from_environment(project_root=tmp_path)

    assert settings.allow_local_repository_sources is True
    assert settings.repository_clone_timeout_seconds == 120
    assert settings.repository_git_output_limit_bytes == 128 * 1024


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("LINEAGE_ALLOW_LOCAL_REPOSITORY_SOURCES", "secret/path"),
        ("LINEAGE_REPOSITORY_CLONE_TIMEOUT_SECONDS", "0"),
        ("LINEAGE_REPOSITORY_CLONE_TIMEOUT_SECONDS", "601"),
        ("LINEAGE_REPOSITORY_CLONE_TIMEOUT_SECONDS", " 60"),
        ("LINEAGE_REPOSITORY_GIT_OUTPUT_LIMIT_BYTES", "0"),
        ("LINEAGE_REPOSITORY_GIT_OUTPUT_LIMIT_BYTES", "1048577"),
        ("LINEAGE_REPOSITORY_GIT_OUTPUT_LIMIT_BYTES", "not-a-number"),
    ),
)
def test_repository_source_settings_reject_invalid_values_without_echoing_them(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    for environment_name in _POLICY_ENVIRONMENT:
        monkeypatch.delenv(environment_name, raising=False)
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError) as captured:
        Settings.from_environment(project_root=tmp_path)

    assert value not in str(captured.value)
