from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lineage_api.db import Database
from lineage_api.domain.errors import DomainError


ARTIFACT = "sha256:" + "a" * 64
OTHER_ARTIFACT = "sha256:" + "c" * 64
PACKAGE_DIGEST = "sha256:" + "b" * 64
OTHER_PACKAGE_DIGEST = "sha256:" + "d" * 64


class MutableClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


def _service_type():
    try:
        from lineage_api.services.runtime_policy import RuntimePolicyService
    except ModuleNotFoundError:
        pytest.fail("RuntimePolicyService is not implemented")
    return RuntimePolicyService


@pytest.fixture
def policy(tmp_path: Path):
    database = Database(tmp_path / "lineage.db")
    database.initialize()
    clock = MutableClock()
    service = _service_type()(database, signing_secret="runtime-policy-secret", clock=clock)
    return service, database, clock


def _profile(
    *,
    profile_id: str = "payments-service-otel",
    environment: str = "production",
    environment_class: str = "PRODUCTION",
    mechanism: str = "OTEL",
) -> dict[str, object]:
    return {
        "schemaVersion": "1.0.0",
        "profileId": profile_id,
        "profileVersion": "1.0.0",
        "owner": "payments-platform",
        "workloadId": "payments-service",
        "workloadIdentity": "spiffe://lineage.local/workload/payments-service",
        "repo": "payments-service",
        "environment": environment,
        "environmentClass": environment_class,
        "mechanism": mechanism,
        "installMode": "SHARED_COLLECTOR" if mechanism == "OTEL" else "EMBEDDED",
        "framework": {"name": "fastapi", "versionRange": ">=0.116,<0.117"},
        "allowedDatasets": [
            "snowflake://payments/raw.transactions",
            "snowflake://payments/analytics.daily_revenue",
        ],
        "allowedAttributes": [
            "db.system.name",
            "db.namespace",
            "db.collection.name",
        ],
        "artifactIdentityStrategy": "DIGEST",
        "permittedGranularity": ["CONNECTIVITY", "DATASET"],
        "parserContracts": [],
        "bufferBudget": {
            "maxRecords": 1000,
            "maxBytes": 1048576,
            "enqueueTimeoutMs": 2,
            "maxRetries": 3,
            "drainTimeoutSeconds": 10,
        },
        "overheadBudget": {
            "maxCpuPercent": 5,
            "maxMemoryBytes": 67108864,
            "maxP95EnqueueMillis": 2,
        },
        "deploymentCriticality": "REQUIRED",
        "canaryPercent": 5,
        "packageDigest": PACKAGE_DIGEST,
    }


def _issue(service, profile: dict[str, object], **overrides):
    request = {
        "profile_id": profile["profileId"],
        "profile_version": profile["profileVersion"],
        "workload_id": profile["workloadId"],
        "repo": profile["repo"],
        "environment": profile["environment"],
        "artifact_digest": ARTIFACT,
        "mechanism": profile["mechanism"],
        "identity": profile["workloadIdentity"],
        "datasets": tuple(profile["allowedDatasets"]),
        "ttl_seconds": 300,
        "actor": "deployment/payments-v42",
    }
    request.update(overrides)
    return service.issue_lease(**request)


def _attest(service, profile: dict[str, object], artifact_digest: str = ARTIFACT) -> dict[str, object]:
    return service.attest_artifact(
        profile_id=str(profile["profileId"]),
        profile_version=str(profile["profileVersion"]),
        artifact_digest=artifact_digest,
        evidence_ref="object://deployments/payments-v42/attestation",
        actor="deployment-controller",
    )


def _activate(service, profile: dict[str, object]) -> dict[str, object]:
    registered = service.register_profile(profile, actor="runtime-admin")
    _attest(service, profile)
    service.enable_profile(
        profile_id=str(profile["profileId"]),
        profile_version=str(profile["profileVersion"]),
        actor="runtime-admin",
        reason="ATDD and owner approval complete",
    )
    return registered


def _validate(service, lease: dict[str, object], profile: dict[str, object], **overrides):
    context = {
        "identity": profile["workloadIdentity"],
        "artifact_digest": lease["artifactDigest"],
        "environment": profile["environment"],
        "mechanism": profile["mechanism"],
        "datasets": tuple(lease["datasets"]),
        "window_id": lease["windowId"],
    }
    context.update(overrides)
    return service.validate_lease(lease["leaseId"], lease["token"], **context)


def test_production_profile_is_disabled_by_default_then_issues_exact_short_lease(policy) -> None:
    service, _, _ = policy
    profile = _profile()
    registered = service.register_profile(profile, actor="runtime-admin")
    _attest(service, profile)

    assert registered["state"] == "DISABLED"
    assert registered["profileDigest"].startswith("sha256:")
    with pytest.raises(DomainError, match="RUNTIME_PROFILE_DISABLED"):
        _issue(service, profile)

    enabled = service.enable_profile(
        profile_id=str(profile["profileId"]),
        profile_version=str(profile["profileVersion"]),
        actor="runtime-admin",
        reason="ATDD and owner approval complete",
    )
    lease = _issue(service, profile)

    assert enabled["state"] == "ENABLED"
    assert lease["state"] == "ACTIVE"
    assert lease["profileDigest"] == registered["profileDigest"]
    assert lease["workloadId"] == "payments-service"
    assert lease["artifactDigest"] == ARTIFACT
    assert lease["workloadIdentity"] == profile["workloadIdentity"]
    assert lease["windowId"].startswith("runtime-window-")
    assert lease["policyEpoch"] >= 1
    assert lease["expiresAt"] == "2026-08-07T12:05:00Z"
    assert _validate(service, lease, profile)["leaseId"] == lease["leaseId"]


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"workload_id": "other-service"}, "RUNTIME_WORKLOAD_MISMATCH"),
        ({"repo": "other-repo"}, "RUNTIME_REPO_MISMATCH"),
        ({"environment": "staging"}, "RUNTIME_ENVIRONMENT_MISMATCH"),
        ({"mechanism": "SDK"}, "RUNTIME_MECHANISM_MISMATCH"),
        ({"identity": "spiffe://lineage.local/workload/other"}, "RUNTIME_IDENTITY_MISMATCH"),
        ({"artifact_digest": "payments-v42"}, "RUNTIME_ARTIFACT_INVALID"),
        ({"artifact_digest": OTHER_ARTIFACT}, "RUNTIME_ARTIFACT_NOT_ATTESTED"),
        ({"datasets": ("snowflake://outside/scope",)}, "RUNTIME_DATASET_SCOPE_VIOLATION"),
        ({"ttl_seconds": 3601}, "RUNTIME_LEASE_TTL_INVALID"),
    ],
)
def test_lease_fails_closed_on_identity_scope_and_artifact_mismatch(
    policy, override: dict[str, object], code: str
) -> None:
    service, _, _ = policy
    profile = _profile()
    _activate(service, profile)

    with pytest.raises(DomainError, match=code):
        _issue(service, profile, **override)


@pytest.mark.parametrize(
    ("scope_type", "scope_value"),
    [
        ("GLOBAL", "*"),
        ("ENVIRONMENT", "production"),
        ("WORKLOAD", "payments-service"),
        ("MECHANISM", "OTEL"),
        ("PROFILE", "payments-service-otel@1.0.0"),
        ("DATASET", "snowflake://payments/raw.transactions"),
    ],
)
def test_hierarchical_kill_switch_revokes_active_leases_and_blocks_new_ones(
    policy, scope_type: str, scope_value: str
) -> None:
    service, database, _ = policy
    profile = _profile()
    _activate(service, profile)
    lease = _issue(service, profile)

    disabled = service.set_kill_switch(
        scope_type=scope_type,
        scope_value=scope_value,
        active=True,
        actor="incident-commander",
        reason="runtime safety stop",
    )

    assert disabled["active"] is True
    with pytest.raises(DomainError, match="RUNTIME_COLLECTION_DISABLED"):
        _validate(service, lease, profile)
    with pytest.raises(DomainError, match="RUNTIME_COLLECTION_DISABLED"):
        _issue(service, profile)
    with database.connection() as connection:
        stored = connection.execute(
            "SELECT state, revoked_reason FROM runtime_leases WHERE lease_id = ?",
            (lease["leaseId"],),
        ).fetchone()
        audit = connection.execute(
            "SELECT action, payload_json FROM audit_events WHERE resource_type = 'runtime_policy'"
        ).fetchall()
    assert (stored["state"], stored["revoked_reason"]) == ("DISABLED", "runtime safety stop")
    assert any(row["action"] == "RUNTIME_KILL_SWITCH_ENABLED" for row in audit)


def test_dataset_kill_switch_isolates_other_dataset_leases(policy) -> None:
    service, _, _ = policy
    profile = _profile()
    _activate(service, profile)
    dataset_a, dataset_b = profile["allowedDatasets"]
    lease_a = _issue(service, profile, datasets=(dataset_a,))
    lease_b = _issue(service, profile, datasets=(dataset_b,))

    service.set_kill_switch(
        scope_type="DATASET",
        scope_value=dataset_a,
        active=True,
        actor="incident-commander",
        reason="isolate one dataset",
    )

    with pytest.raises(DomainError, match="RUNTIME_COLLECTION_DISABLED"):
        _validate(service, lease_a, profile)
    assert _validate(service, lease_b, profile)["leaseId"] == lease_b["leaseId"]
    assert [item["leaseId"] for item in service.active_leases()] == [lease_b["leaseId"]]


def test_replaying_inactive_kill_switch_does_not_fence_valid_lease(policy) -> None:
    service, _, _ = policy
    profile = _profile()
    _activate(service, profile)
    lease = _issue(service, profile)

    service.set_kill_switch(
        scope_type="ENVIRONMENT",
        scope_value="production",
        active=False,
        actor="runtime-admin",
        reason="confirm collection remains enabled",
    )

    assert _validate(service, lease, profile)["leaseId"] == lease["leaseId"]
    assert [item["leaseId"] for item in service.active_leases()] == [lease["leaseId"]]


def test_production_switch_does_not_disable_independent_atdd_profile(policy) -> None:
    service, _, _ = policy
    production = _profile()
    atdd = _profile(
        profile_id="payments-service-atdd-sdk",
        environment="atdd",
        environment_class="ATDD",
        mechanism="SDK",
    )
    for profile in (production, atdd):
        _activate(service, profile)
    service.set_kill_switch(
        scope_type="ENVIRONMENT",
        scope_value="production",
        active=True,
        actor="incident-commander",
        reason="production only",
    )

    atdd_lease = _issue(service, atdd)

    assert atdd_lease["environment"] == "atdd"
    with pytest.raises(DomainError, match="RUNTIME_COLLECTION_DISABLED"):
        _issue(service, production)


def test_profile_disable_revokes_only_its_leases_and_active_listing_is_truthful(policy) -> None:
    service, _, _ = policy
    production = _profile()
    atdd = _profile(
        profile_id="payments-service-atdd-sdk",
        environment="atdd",
        environment_class="ATDD",
        mechanism="SDK",
    )
    for profile in (production, atdd):
        _activate(service, profile)
    production_lease = _issue(service, production)
    atdd_lease = _issue(service, atdd)
    assert {item["leaseId"] for item in service.active_leases()} == {
        production_lease["leaseId"],
        atdd_lease["leaseId"],
    }

    disabled = service.disable_profile(
        profile_id=str(production["profileId"]),
        profile_version=str(production["profileVersion"]),
        actor="runtime-admin",
        reason="owner disabled production collection",
    )

    assert disabled["state"] == "DISABLED"
    assert [item["leaseId"] for item in service.active_leases()] == [atdd_lease["leaseId"]]
    with pytest.raises(DomainError, match="RUNTIME_COLLECTION_DISABLED"):
        _validate(service, production_lease, production)
    assert _validate(service, atdd_lease, atdd)["leaseId"] == atdd_lease["leaseId"]


def test_profile_registration_is_idempotent_but_version_identity_cannot_conflict(policy) -> None:
    service, _, _ = policy
    profile = _profile()

    first = service.register_profile(profile, actor="runtime-admin")
    replay = service.register_profile(profile, actor="runtime-admin")

    assert replay == first
    changed = {**profile, "packageDigest": OTHER_PACKAGE_DIGEST}
    with pytest.raises(DomainError, match="RUNTIME_PROFILE_CONFLICT"):
        service.register_profile(changed, actor="runtime-admin")


def test_profile_service_rejects_version_outside_shared_semver_contract(policy) -> None:
    service, database, _ = policy
    profile = {**_profile(), "profileVersion": "latest"}

    with pytest.raises(ValueError, match="profileVersion"):
        service.register_profile(profile, actor="runtime-admin")

    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM runtime_profiles").fetchone()[0] == 0


def test_profile_registration_rejects_invalid_nested_budget_before_persisting(policy) -> None:
    service, database, _ = policy
    profile = _profile()
    profile["bufferBudget"] = {**profile["bufferBudget"], "maxRecords": 0}

    with pytest.raises(ValueError, match="bufferBudget.maxRecords"):
        service.register_profile(profile, actor="runtime-admin")

    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM runtime_profiles").fetchone()[0] == 0


def test_profile_owns_the_exact_configurable_workload_identity(policy) -> None:
    service, _, _ = policy
    profile = {**_profile(), "workloadIdentity": "arn:aws:iam::123456789012:role/payments-runtime"}
    _activate(service, profile)

    lease = _issue(service, profile, identity=profile["workloadIdentity"])

    assert lease["workloadId"] == "payments-service"


def test_lease_expiry_is_durable_and_renewal_uses_current_policy(policy) -> None:
    service, database, clock = policy
    profile = _profile()
    _activate(service, profile)
    lease = _issue(service, profile, ttl_seconds=60)
    renewed = service.renew_lease(
        lease["leaseId"],
        lease["token"],
        identity=profile["workloadIdentity"],
        artifact_digest=lease["artifactDigest"],
        environment=profile["environment"],
        mechanism=profile["mechanism"],
        datasets=tuple(lease["datasets"]),
        window_id=lease["windowId"],
        ttl_seconds=120,
    )
    assert renewed["expiresAt"] == "2026-08-07T12:02:00Z"

    clock.advance(121)
    with pytest.raises(DomainError, match="RUNTIME_LEASE_EXPIRED"):
        _validate(service, renewed, profile)
    with database.connection() as connection:
        state = connection.execute(
            "SELECT state FROM runtime_leases WHERE lease_id = ?", (lease["leaseId"],)
        ).fetchone()["state"]
        expiry_audit = connection.execute(
            "SELECT payload_json FROM audit_events WHERE action = 'RUNTIME_LEASE_EXPIRED'"
        ).fetchone()
    assert state == "EXPIRED"
    assert expiry_audit is not None


def test_renewal_cannot_revive_lease_expiring_between_validation_and_write(
    policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, database, clock = policy
    profile = _profile()
    _activate(service, profile)
    lease = _issue(service, profile, ttl_seconds=60)
    validate = service.validate_lease

    def validate_then_expire(*args, **kwargs):
        claims = validate(*args, **kwargs)
        clock.advance(61)
        return claims

    monkeypatch.setattr(service, "validate_lease", validate_then_expire)

    with pytest.raises(DomainError, match="RUNTIME_LEASE_EXPIRED"):
        service.renew_lease(
            lease["leaseId"],
            lease["token"],
            identity=profile["workloadIdentity"],
            artifact_digest=lease["artifactDigest"],
            environment=profile["environment"],
            mechanism=profile["mechanism"],
            datasets=tuple(lease["datasets"]),
            window_id=lease["windowId"],
            ttl_seconds=120,
        )

    with database.connection() as connection:
        stored = connection.execute(
            "SELECT state, expires_at FROM runtime_leases WHERE lease_id = ?",
            (lease["leaseId"],),
        ).fetchone()
    assert (stored["state"], stored["expires_at"]) == (
        "EXPIRED",
        "2026-08-07T12:01:00Z",
    )


def test_renewal_samples_expiry_after_waiting_for_write_lock(
    policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, database, clock = policy
    profile = _profile()
    _activate(service, profile)
    lease = _issue(service, profile, ttl_seconds=60)
    transaction = database.transaction
    transaction_count = 0

    @contextmanager
    def contended_transaction():
        nonlocal transaction_count
        transaction_count += 1
        if transaction_count == 2:
            clock.advance(61)
        with transaction() as connection:
            yield connection

    monkeypatch.setattr(database, "transaction", contended_transaction)

    with pytest.raises(DomainError, match="RUNTIME_LEASE_EXPIRED"):
        service.renew_lease(
            lease["leaseId"],
            lease["token"],
            identity=profile["workloadIdentity"],
            artifact_digest=lease["artifactDigest"],
            environment=profile["environment"],
            mechanism=profile["mechanism"],
            datasets=tuple(lease["datasets"]),
            window_id=lease["windowId"],
            ttl_seconds=120,
        )

    assert service.active_leases() == []


def test_active_listing_samples_expiry_after_waiting_for_write_lock(
    policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, database, clock = policy
    profile = _profile()
    _activate(service, profile)
    _issue(service, profile, ttl_seconds=60)
    transaction = database.transaction

    @contextmanager
    def contended_transaction():
        clock.advance(61)
        with transaction() as connection:
            yield connection

    monkeypatch.setattr(database, "transaction", contended_transaction)

    assert service.active_leases() == []


def test_lease_validation_rebinds_principal_artifact_scope_and_window(policy) -> None:
    service, _, _ = policy
    profile = _profile()
    _activate(service, profile)
    lease = _issue(service, profile)

    invalid_contexts = [
        ({"identity": "spiffe://lineage.local/workload/other"}, "RUNTIME_IDENTITY_MISMATCH"),
        ({"artifact_digest": OTHER_ARTIFACT}, "RUNTIME_ARTIFACT_MISMATCH"),
        ({"environment": "atdd"}, "RUNTIME_ENVIRONMENT_MISMATCH"),
        ({"mechanism": "SDK"}, "RUNTIME_MECHANISM_MISMATCH"),
        ({"datasets": ("snowflake://outside/scope",)}, "RUNTIME_DATASET_SCOPE_VIOLATION"),
        ({"window_id": "runtime-window-other"}, "RUNTIME_WINDOW_MISMATCH"),
    ]
    for overrides, code in invalid_contexts:
        with pytest.raises(DomainError, match=code):
            _validate(service, lease, profile, **overrides)


def test_lease_hmac_is_verified_even_if_stored_bearer_digest_is_replaced(policy) -> None:
    import hashlib

    service, database, _ = policy
    profile = _profile()
    _activate(service, profile)
    lease = _issue(service, profile)
    tampered = lease["token"][:-1] + ("0" if lease["token"][-1] != "0" else "1")
    with database.transaction() as connection:
        connection.execute(
            "UPDATE runtime_leases SET token_digest = ? WHERE lease_id = ?",
            (hashlib.sha256(tampered.encode()).hexdigest(), lease["leaseId"]),
        )

    with pytest.raises(DomainError, match="RUNTIME_LEASE_SIGNATURE_INVALID"):
        _validate(service, {**lease, "token": tampered}, profile)


def test_lease_audit_reconstructs_exact_granted_authority(policy) -> None:
    import json

    service, database, _ = policy
    profile = _profile()
    _activate(service, profile)
    lease = _issue(service, profile)

    with database.connection() as connection:
        row = connection.execute(
            "SELECT payload_json FROM audit_events WHERE action = 'RUNTIME_LEASE_ISSUED'"
        ).fetchone()
    audit = json.loads(row["payload_json"])
    assert audit == {
        "artifactDigest": ARTIFACT,
        "datasets": sorted(profile["allowedDatasets"]),
        "expiresAt": lease["expiresAt"],
        "mechanism": profile["mechanism"],
        "policyEpoch": lease["policyEpoch"],
        "profileDigest": lease["profileDigest"],
        "profileId": profile["profileId"],
        "profileVersion": profile["profileVersion"],
        "windowId": lease["windowId"],
        "workloadIdentity": profile["workloadIdentity"],
    }


def test_artifact_attestation_is_immutable_and_has_explicit_revocation_lifecycle(policy) -> None:
    service, database, _ = policy
    profile = _profile()
    service.register_profile(profile, actor="runtime-admin")
    first = _attest(service, profile)
    replay = _attest(service, profile)
    service.enable_profile(
        profile_id=str(profile["profileId"]),
        profile_version=str(profile["profileVersion"]),
        actor="runtime-admin",
        reason="approved",
    )
    lease = _issue(service, profile)

    assert replay == first
    with pytest.raises(DomainError, match="RUNTIME_ARTIFACT_ATTESTATION_CONFLICT"):
        service.attest_artifact(
            profile_id=str(profile["profileId"]),
            profile_version=str(profile["profileVersion"]),
            artifact_digest=ARTIFACT,
            evidence_ref="object://deployments/other/attestation",
            actor="deployment-controller",
        )

    revoked = service.revoke_artifact(
        profile_id=str(profile["profileId"]),
        profile_version=str(profile["profileVersion"]),
        artifact_digest=ARTIFACT,
        actor="security-operator",
        reason="deployment attestation withdrawn",
    )
    assert revoked["state"] == "REVOKED"
    with pytest.raises(DomainError, match="RUNTIME_LEASE_REVOKED"):
        _validate(service, lease, profile)
    with pytest.raises(DomainError, match="RUNTIME_ARTIFACT_NOT_ATTESTED"):
        _issue(service, profile)
    with pytest.raises(DomainError, match="RUNTIME_ARTIFACT_REVOKED"):
        _attest(service, profile)

    approved = service.reactivate_artifact(
        profile_id=str(profile["profileId"]),
        profile_version=str(profile["profileVersion"]),
        artifact_digest=ARTIFACT,
        evidence_ref="object://deployments/payments-v42/reattestation",
        actor="security-operator",
        reason="fresh evidence approved",
    )
    assert approved["state"] == "APPROVED"
    assert _validate(service, _issue(service, profile), profile)["state"] == "ACTIVE"

    with database.connection() as connection:
        actions = {
            row["action"]
            for row in connection.execute(
                "SELECT action FROM audit_events WHERE resource_type = 'runtime_policy'"
            ).fetchall()
        }
    assert {
        "RUNTIME_ARTIFACT_ATTESTED",
        "RUNTIME_ARTIFACT_REVOKED",
        "RUNTIME_ARTIFACT_REACTIVATED",
    } <= actions
