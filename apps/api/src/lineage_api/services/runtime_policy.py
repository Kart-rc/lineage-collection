from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from lineage_api.application.models import parse_utc
from lineage_api.db import Database
from lineage_api.domain.errors import DomainError


_PROFILE_REQUIRED = {
    "schemaVersion",
    "profileId",
    "profileVersion",
    "owner",
    "workloadId",
    "workloadIdentity",
    "repo",
    "environment",
    "environmentClass",
    "mechanism",
    "installMode",
    "framework",
    "allowedDatasets",
    "allowedAttributes",
    "artifactIdentityStrategy",
    "permittedGranularity",
    "parserContracts",
    "bufferBudget",
    "overheadBudget",
    "deploymentCriticality",
    "canaryPercent",
    "packageDigest",
}
_MECHANISMS = {"OPENLINEAGE", "SDK", "OTEL", "DASK"}
_ENVIRONMENT_CLASSES = {"PRODUCTION", "ATDD", "NON_PRODUCTION"}
_KILL_SCOPES = {"GLOBAL", "ENVIRONMENT", "WORKLOAD", "MECHANISM", "PROFILE", "DATASET"}
_INSTALL_MODES = {
    "EMBEDDED",
    "SHARED_COLLECTOR",
    "SIDECAR",
    "FRAMEWORK_PLUGIN",
    "EXISTING_COLLECTOR",
}
_GRANULARITIES = {"CONNECTIVITY", "DATASET", "ELEMENT"}
_SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode()).hexdigest()


def _utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("runtime policy timestamps require a timezone")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 71 or not value.startswith("sha256:"):
        return False
    return all(character in "0123456789abcdef" for character in value[7:])


class RuntimePolicyService:
    """Durable profile, attestation, lease and kill-switch authority."""

    def __init__(self, database: Database, *, signing_secret: str, clock: object) -> None:
        if not signing_secret:
            raise ValueError("runtime policy signing secret must be non-empty")
        self._database = database
        self._secret = signing_secret.encode()
        self._clock = clock

    def register_profile(self, profile: dict[str, object], *, actor: str) -> dict[str, object]:
        self._validate_profile(profile)
        if not actor:
            raise ValueError("runtime profile actor must be non-empty")
        profile_id = str(profile["profileId"])
        version = str(profile["profileVersion"])
        profile_digest = _digest(profile)
        now = _utc(self._now())
        with self._database.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM runtime_profiles WHERE profile_id = ? AND profile_version = ?",
                (profile_id, version),
            ).fetchone()
            if existing is not None:
                if existing["profile_digest"] != profile_digest:
                    raise DomainError(
                        "RUNTIME_PROFILE_CONFLICT",
                        "Runtime profile identity and version were reused with different content",
                        profile_id,
                    )
                return {
                    **json.loads(existing["payload_json"]),
                    "profileDigest": existing["profile_digest"],
                    "policyEpoch": int(existing["policy_epoch"]),
                    "state": existing["state"],
                }
            connection.execute(
                """
                INSERT INTO runtime_profiles(
                    profile_id, profile_version, profile_digest, payload_json, state,
                    policy_epoch, actor, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'DISABLED', 0, ?, ?, ?)
                """,
                (profile_id, version, profile_digest, _canonical(profile), actor, now, now),
            )
            self._audit(
                connection,
                actor=actor,
                action="RUNTIME_PROFILE_REGISTERED",
                resource_id=f"{profile_id}@{version}",
                payload={"profileDigest": profile_digest, "policyEpoch": 0},
                now=now,
            )
        return {
            **profile,
            "profileDigest": profile_digest,
            "policyEpoch": 0,
            "state": "DISABLED",
        }

    def enable_profile(
        self,
        *,
        profile_id: str,
        profile_version: str,
        actor: str,
        reason: str,
    ) -> dict[str, object]:
        if not all((actor, reason)):
            raise ValueError("runtime profile enablement requires actor and reason")
        now = _utc(self._now())
        with self._database.transaction() as connection:
            row = self._profile_in(connection, profile_id, profile_version)
            if row["state"] == "ENABLED":
                return {
                    "profileId": profile_id,
                    "profileVersion": profile_version,
                    "policyEpoch": int(row["policy_epoch"]),
                    "state": "ENABLED",
                }
            epoch = int(row["policy_epoch"]) + 1
            connection.execute(
                """
                UPDATE runtime_profiles
                SET state = 'ENABLED', policy_epoch = ?, enabled_by = ?,
                    enabled_reason = ?, updated_at = ?
                WHERE profile_id = ? AND profile_version = ?
                """,
                (epoch, actor, reason, now, profile_id, profile_version),
            )
            self._audit(
                connection,
                actor=actor,
                action="RUNTIME_PROFILE_ENABLED",
                resource_id=f"{profile_id}@{profile_version}",
                payload={"reason": reason, "policyEpoch": epoch},
                now=now,
            )
        return {
            "profileId": profile_id,
            "profileVersion": profile_version,
            "policyEpoch": epoch,
            "state": "ENABLED",
        }

    def disable_profile(
        self,
        *,
        profile_id: str,
        profile_version: str,
        actor: str,
        reason: str,
    ) -> dict[str, object]:
        if not all((actor, reason)):
            raise ValueError("runtime profile disablement requires actor and reason")
        now = _utc(self._now())
        with self._database.transaction() as connection:
            row = self._profile_in(connection, profile_id, profile_version)
            if row["state"] == "DISABLED":
                return {
                    "profileId": profile_id,
                    "profileVersion": profile_version,
                    "policyEpoch": int(row["policy_epoch"]),
                    "state": "DISABLED",
                }
            epoch = int(row["policy_epoch"]) + 1
            connection.execute(
                """
                UPDATE runtime_profiles
                SET state = 'DISABLED', policy_epoch = ?, enabled_by = NULL,
                    enabled_reason = ?, updated_at = ?
                WHERE profile_id = ? AND profile_version = ?
                """,
                (epoch, reason, now, profile_id, profile_version),
            )
            connection.execute(
                """
                UPDATE runtime_leases
                SET state = 'DISABLED', revoked_reason = ?, updated_at = ?
                WHERE profile_id = ? AND profile_version = ? AND state = 'ACTIVE'
                """,
                (reason, now, profile_id, profile_version),
            )
            self._audit(
                connection,
                actor=actor,
                action="RUNTIME_PROFILE_DISABLED",
                resource_id=f"{profile_id}@{profile_version}",
                payload={"reason": reason, "policyEpoch": epoch},
                now=now,
            )
        return {
            "profileId": profile_id,
            "profileVersion": profile_version,
            "policyEpoch": epoch,
            "state": "DISABLED",
        }

    def attest_artifact(
        self,
        *,
        profile_id: str,
        profile_version: str,
        artifact_digest: str,
        evidence_ref: str,
        actor: str,
    ) -> dict[str, object]:
        if not _is_sha256(artifact_digest):
            raise DomainError(
                "RUNTIME_ARTIFACT_INVALID",
                "Runtime artifact attestation requires a lowercase SHA-256 digest",
                profile_id,
            )
        if not all((evidence_ref, actor)):
            raise ValueError("runtime artifact attestation requires evidence and actor")
        now = _utc(self._now())
        with self._database.transaction() as connection:
            self._profile_in(connection, profile_id, profile_version)
            existing = connection.execute(
                """
                SELECT * FROM runtime_artifact_attestations
                WHERE profile_id = ? AND profile_version = ? AND artifact_digest = ?
                """,
                (profile_id, profile_version, artifact_digest),
            ).fetchone()
            if existing is not None:
                if existing["state"] == "REVOKED":
                    raise DomainError(
                        "RUNTIME_ARTIFACT_REVOKED",
                        "Revoked runtime artifact requires explicit reactivation",
                        profile_id,
                    )
                if existing["evidence_ref"] != evidence_ref:
                    raise DomainError(
                        "RUNTIME_ARTIFACT_ATTESTATION_CONFLICT",
                        "Runtime artifact attestation evidence is immutable",
                        profile_id,
                    )
                return {
                    "profileId": profile_id,
                    "profileVersion": profile_version,
                    "artifactDigest": artifact_digest,
                    "evidenceRef": existing["evidence_ref"],
                    "state": existing["state"],
                }
            connection.execute(
                """
                INSERT INTO runtime_artifact_attestations(
                    profile_id, profile_version, artifact_digest, state,
                    evidence_ref, actor, created_at, updated_at
                ) VALUES (?, ?, ?, 'APPROVED', ?, ?, ?, ?)
                """,
                (
                    profile_id,
                    profile_version,
                    artifact_digest,
                    evidence_ref,
                    actor,
                    now,
                    now,
                ),
            )
            self._audit(
                connection,
                actor=actor,
                action="RUNTIME_ARTIFACT_ATTESTED",
                resource_id=f"{profile_id}@{profile_version}:{artifact_digest}",
                payload={"artifactDigest": artifact_digest, "evidenceRef": evidence_ref},
                now=now,
            )
        return {
            "profileId": profile_id,
            "profileVersion": profile_version,
            "artifactDigest": artifact_digest,
            "evidenceRef": evidence_ref,
            "state": "APPROVED",
        }

    def revoke_artifact(
        self,
        *,
        profile_id: str,
        profile_version: str,
        artifact_digest: str,
        actor: str,
        reason: str,
    ) -> dict[str, object]:
        if not _is_sha256(artifact_digest) or not all((actor, reason)):
            raise ValueError("artifact revocation requires an exact digest, actor and reason")
        now = _utc(self._now())
        with self._database.transaction() as connection:
            self._profile_in(connection, profile_id, profile_version)
            attestation = connection.execute(
                """
                SELECT * FROM runtime_artifact_attestations
                WHERE profile_id = ? AND profile_version = ? AND artifact_digest = ?
                """,
                (profile_id, profile_version, artifact_digest),
            ).fetchone()
            if attestation is None:
                raise DomainError(
                    "RUNTIME_ARTIFACT_NOT_ATTESTED",
                    "Runtime artifact has no attestation to revoke",
                    profile_id,
                )
            if attestation["state"] == "REVOKED":
                return {
                    "profileId": profile_id,
                    "profileVersion": profile_version,
                    "artifactDigest": artifact_digest,
                    "reason": attestation["revoked_reason"],
                    "state": "REVOKED",
                }
            connection.execute(
                """
                UPDATE runtime_artifact_attestations
                SET state = 'REVOKED', actor = ?, revoked_reason = ?, revoked_at = ?,
                    updated_at = ?
                WHERE profile_id = ? AND profile_version = ? AND artifact_digest = ?
                """,
                (actor, reason, now, now, profile_id, profile_version, artifact_digest),
            )
            affected = connection.execute(
                """
                UPDATE runtime_leases
                SET state = 'REVOKED', revoked_reason = ?, updated_at = ?
                WHERE profile_id = ? AND profile_version = ? AND artifact_digest = ?
                    AND state = 'ACTIVE'
                """,
                (reason, now, profile_id, profile_version, artifact_digest),
            ).rowcount
            self._audit(
                connection,
                actor=actor,
                action="RUNTIME_ARTIFACT_REVOKED",
                resource_id=f"{profile_id}@{profile_version}:{artifact_digest}",
                payload={
                    "affectedLeases": affected,
                    "artifactDigest": artifact_digest,
                    "reason": reason,
                },
                now=now,
            )
        return {
            "profileId": profile_id,
            "profileVersion": profile_version,
            "artifactDigest": artifact_digest,
            "reason": reason,
            "state": "REVOKED",
        }

    def reactivate_artifact(
        self,
        *,
        profile_id: str,
        profile_version: str,
        artifact_digest: str,
        evidence_ref: str,
        actor: str,
        reason: str,
    ) -> dict[str, object]:
        if not _is_sha256(artifact_digest) or not all((evidence_ref, actor, reason)):
            raise ValueError(
                "artifact reactivation requires an exact digest, evidence, actor and reason"
            )
        now = _utc(self._now())
        with self._database.transaction() as connection:
            self._profile_in(connection, profile_id, profile_version)
            attestation = connection.execute(
                """
                SELECT * FROM runtime_artifact_attestations
                WHERE profile_id = ? AND profile_version = ? AND artifact_digest = ?
                """,
                (profile_id, profile_version, artifact_digest),
            ).fetchone()
            if attestation is None:
                raise DomainError(
                    "RUNTIME_ARTIFACT_NOT_ATTESTED",
                    "Runtime artifact must be attested before reactivation",
                    profile_id,
                )
            if attestation["state"] != "REVOKED":
                raise DomainError(
                    "RUNTIME_ARTIFACT_ALREADY_APPROVED",
                    "Runtime artifact is already approved",
                    profile_id,
                )
            connection.execute(
                """
                UPDATE runtime_artifact_attestations
                SET state = 'APPROVED', evidence_ref = ?, actor = ?, revoked_reason = NULL,
                    revoked_at = NULL, updated_at = ?
                WHERE profile_id = ? AND profile_version = ? AND artifact_digest = ?
                """,
                (evidence_ref, actor, now, profile_id, profile_version, artifact_digest),
            )
            self._audit(
                connection,
                actor=actor,
                action="RUNTIME_ARTIFACT_REACTIVATED",
                resource_id=f"{profile_id}@{profile_version}:{artifact_digest}",
                payload={
                    "artifactDigest": artifact_digest,
                    "evidenceRef": evidence_ref,
                    "reason": reason,
                },
                now=now,
            )
        return {
            "profileId": profile_id,
            "profileVersion": profile_version,
            "artifactDigest": artifact_digest,
            "evidenceRef": evidence_ref,
            "state": "APPROVED",
        }

    def active_leases(self) -> list[dict[str, object]]:
        with self._database.transaction() as connection:
            now = _utc(self._now())
            due = connection.execute(
                "SELECT lease_id FROM runtime_leases WHERE state = 'ACTIVE' AND expires_at <= ?",
                (now,),
            ).fetchall()
            for row in due:
                self._expire_in(connection, row["lease_id"], now)
            rows = connection.execute(
                """
                SELECT leases.payload_json
                FROM runtime_leases AS leases
                JOIN runtime_profiles AS profiles
                  ON profiles.profile_id = leases.profile_id
                 AND profiles.profile_version = leases.profile_version
                WHERE leases.state = 'ACTIVE'
                  AND profiles.state = 'ENABLED'
                  AND profiles.policy_epoch = leases.policy_epoch
                  AND leases.expires_at > ?
                ORDER BY leases.issued_at, leases.lease_id
                """,
                (now,),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def issue_lease(
        self,
        *,
        profile_id: str,
        profile_version: str,
        workload_id: str,
        repo: str,
        environment: str,
        artifact_digest: str,
        mechanism: str,
        identity: str,
        datasets: tuple[str, ...],
        ttl_seconds: int,
        actor: str,
    ) -> dict[str, object]:
        now_value = self._now()
        now = _utc(now_value)
        lease_id = f"runtime-lease-{uuid.uuid4().hex}"
        window_id = f"runtime-window-{uuid.uuid4().hex}"
        with self._database.transaction() as connection:
            profile_row = self._profile_in(connection, profile_id, profile_version)
            profile = json.loads(profile_row["payload_json"])
            if profile_row["state"] != "ENABLED":
                raise DomainError(
                    "RUNTIME_PROFILE_DISABLED", "Runtime profile is disabled", profile_id
                )
            self._assert_request(
                profile,
                workload_id=workload_id,
                repo=repo,
                environment=environment,
                artifact_digest=artifact_digest,
                mechanism=mechanism,
                identity=identity,
                datasets=datasets,
                ttl_seconds=ttl_seconds,
                actor=actor,
            )
            attestation = connection.execute(
                """
                SELECT state FROM runtime_artifact_attestations
                WHERE profile_id = ? AND profile_version = ? AND artifact_digest = ?
                """,
                (profile_id, profile_version, artifact_digest),
            ).fetchone()
            if attestation is None or attestation["state"] != "APPROVED":
                raise DomainError(
                    "RUNTIME_ARTIFACT_NOT_ATTESTED",
                    "Runtime artifact is not approved for this instrumentation profile",
                    profile_id,
                )
            requested_datasets = tuple(sorted(set(datasets)))
            self._assert_collection_enabled(
                profile, datasets=requested_datasets, connection=connection
            )
            expires = now_value + timedelta(seconds=ttl_seconds)
            policy_epoch = int(profile_row["policy_epoch"])
            claims: dict[str, object] = {
                "schemaVersion": "1.0.0",
                "leaseId": lease_id,
                "profileId": profile_id,
                "profileVersion": profile_version,
                "profileDigest": profile_row["profile_digest"],
                "workloadId": workload_id,
                "workloadIdentity": identity,
                "repo": repo,
                "environment": environment,
                "artifactDigest": artifact_digest,
                "mechanism": mechanism,
                "datasets": list(requested_datasets),
                "permittedGranularity": list(profile["permittedGranularity"]),
                "windowId": window_id,
                "policyEpoch": policy_epoch,
                "issuedAt": now,
                "expiresAt": _utc(expires),
                "state": "ACTIVE",
            }
            token = self._sign(claims)
            connection.execute(
                """
                INSERT INTO runtime_leases(
                    lease_id, token_digest, profile_id, profile_version, profile_digest,
                    workload_id, repo, environment, artifact_digest, mechanism, datasets_json,
                    permitted_granularity_json, workload_identity, window_id, policy_epoch,
                    state, issued_at, expires_at, actor, payload_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?, ?, ?)
                """,
                (
                    lease_id,
                    hashlib.sha256(token.encode()).hexdigest(),
                    profile_id,
                    profile_version,
                    profile_row["profile_digest"],
                    workload_id,
                    repo,
                    environment,
                    artifact_digest,
                    mechanism,
                    _canonical(requested_datasets),
                    _canonical(claims["permittedGranularity"]),
                    identity,
                    window_id,
                    policy_epoch,
                    now,
                    _utc(expires),
                    actor,
                    _canonical(claims),
                    now,
                ),
            )
            self._audit(
                connection,
                actor=actor,
                action="RUNTIME_LEASE_ISSUED",
                resource_id=lease_id,
                payload={
                    "artifactDigest": artifact_digest,
                    "datasets": list(requested_datasets),
                    "expiresAt": claims["expiresAt"],
                    "mechanism": mechanism,
                    "policyEpoch": policy_epoch,
                    "profileDigest": profile_row["profile_digest"],
                    "profileId": profile_id,
                    "profileVersion": profile_version,
                    "windowId": window_id,
                    "workloadIdentity": identity,
                },
                now=now,
            )
        return {**claims, "token": token}

    def validate_lease(
        self,
        lease_id: str,
        token: str,
        *,
        identity: str,
        artifact_digest: str,
        environment: str,
        mechanism: str,
        datasets: tuple[str, ...],
        window_id: str,
    ) -> dict[str, object]:
        expired = False
        claims: dict[str, object] | None = None
        now = _utc(self._now())
        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM runtime_leases WHERE lease_id = ?", (lease_id,)
            ).fetchone()
            if row is None:
                raise DomainError(
                    "RUNTIME_LEASE_NOT_FOUND", "Runtime lease does not exist", lease_id
                )
            self._verify_token(row, token)
            if row["state"] == "DISABLED":
                raise DomainError(
                    "RUNTIME_COLLECTION_DISABLED", "Runtime collection is disabled", lease_id
                )
            if row["state"] == "REVOKED":
                raise DomainError("RUNTIME_LEASE_REVOKED", "Runtime lease is revoked", lease_id)
            if row["state"] == "EXPIRED" or self._now() >= parse_utc(row["expires_at"]):
                if row["state"] == "ACTIVE":
                    self._expire_in(connection, lease_id, now)
                expired = True
            else:
                profile_row = self._profile_in(
                    connection, row["profile_id"], row["profile_version"]
                )
                if profile_row["state"] != "ENABLED" or int(
                    profile_row["policy_epoch"]
                ) != int(row["policy_epoch"]):
                    raise DomainError(
                        "RUNTIME_COLLECTION_DISABLED",
                        "Runtime profile policy fence invalidated this lease",
                        lease_id,
                    )
                profile = json.loads(profile_row["payload_json"])
                self._assert_lease_context(
                    row,
                    identity=identity,
                    artifact_digest=artifact_digest,
                    environment=environment,
                    mechanism=mechanism,
                    datasets=datasets,
                    window_id=window_id,
                )
                self._assert_collection_enabled(
                    profile, datasets=datasets, connection=connection
                )
                claims = json.loads(row["payload_json"])
        if expired:
            raise DomainError("RUNTIME_LEASE_EXPIRED", "Runtime lease is expired", lease_id)
        if claims is None:
            raise RuntimeError("runtime lease validation produced no result")
        return claims

    def renew_lease(
        self,
        lease_id: str,
        token: str,
        *,
        identity: str,
        artifact_digest: str,
        environment: str,
        mechanism: str,
        datasets: tuple[str, ...],
        window_id: str,
        ttl_seconds: int,
    ) -> dict[str, object]:
        if ttl_seconds < 1 or ttl_seconds > 3600:
            raise DomainError(
                "RUNTIME_LEASE_TTL_INVALID",
                "Runtime lease TTL must be between 1 and 3600 seconds",
                lease_id,
            )
        current = self.validate_lease(
            lease_id,
            token,
            identity=identity,
            artifact_digest=artifact_digest,
            environment=environment,
            mechanism=mechanism,
            datasets=datasets,
            window_id=window_id,
        )
        expired = False
        renewed: dict[str, object] | None = None
        renewed_token: str | None = None
        with self._database.transaction() as connection:
            now_value = self._now()
            now = _utc(now_value)
            lease_row = connection.execute(
                "SELECT * FROM runtime_leases WHERE lease_id = ?", (lease_id,)
            ).fetchone()
            if lease_row is None:
                raise DomainError(
                    "RUNTIME_COLLECTION_DISABLED",
                    "Runtime lease stopped accepting renewal",
                    lease_id,
                )
            self._verify_token(lease_row, token)
            if lease_row["state"] == "REVOKED":
                raise DomainError("RUNTIME_LEASE_REVOKED", "Runtime lease is revoked", lease_id)
            if lease_row["state"] != "ACTIVE":
                raise DomainError(
                    "RUNTIME_COLLECTION_DISABLED",
                    "Runtime lease stopped accepting renewal",
                    lease_id,
                )
            if now_value >= parse_utc(lease_row["expires_at"]):
                self._expire_in(connection, lease_id, now)
                expired = True
            else:
                profile_row = self._profile_in(
                    connection, lease_row["profile_id"], lease_row["profile_version"]
                )
                if profile_row["state"] != "ENABLED" or int(
                    profile_row["policy_epoch"]
                ) != int(lease_row["policy_epoch"]):
                    raise DomainError(
                        "RUNTIME_COLLECTION_DISABLED",
                        "Runtime profile policy fence invalidated renewal",
                        lease_id,
                    )
                self._assert_lease_context(
                    lease_row,
                    identity=identity,
                    artifact_digest=artifact_digest,
                    environment=environment,
                    mechanism=mechanism,
                    datasets=datasets,
                    window_id=window_id,
                )
                self._assert_collection_enabled(
                    json.loads(profile_row["payload_json"]),
                    datasets=datasets,
                    connection=connection,
                )
                renewed = {
                    **current,
                    "expiresAt": _utc(now_value + timedelta(seconds=ttl_seconds)),
                    "state": "ACTIVE",
                }
                renewed_token = self._sign(renewed)
                updated = connection.execute(
                    """
                    UPDATE runtime_leases
                    SET token_digest = ?, expires_at = ?, renewed_at = ?, payload_json = ?,
                        actor = ?, updated_at = ?
                    WHERE lease_id = ? AND state = 'ACTIVE' AND expires_at > ?
                    """,
                    (
                        hashlib.sha256(renewed_token.encode()).hexdigest(),
                        renewed["expiresAt"],
                        now,
                        _canonical(renewed),
                        identity,
                        now,
                        lease_id,
                        now,
                    ),
                )
                if updated.rowcount != 1:
                    self._expire_in(connection, lease_id, now)
                    expired = True
                else:
                    self._audit(
                        connection,
                        actor=identity,
                        action="RUNTIME_LEASE_RENEWED",
                        resource_id=lease_id,
                        payload={
                            "artifactDigest": artifact_digest,
                            "expiresAt": renewed["expiresAt"],
                            "policyEpoch": renewed["policyEpoch"],
                            "windowId": window_id,
                        },
                        now=now,
                    )
        if expired:
            raise DomainError("RUNTIME_LEASE_EXPIRED", "Runtime lease is expired", lease_id)
        if renewed is None or renewed_token is None:
            raise RuntimeError("runtime lease renewal produced no result")
        return {**renewed, "token": renewed_token}

    def set_kill_switch(
        self,
        *,
        scope_type: str,
        scope_value: str,
        active: bool,
        actor: str,
        reason: str,
    ) -> dict[str, object]:
        if scope_type not in _KILL_SCOPES or not all((scope_value, actor, reason)):
            raise ValueError("runtime kill switch requires a valid scope, actor and reason")
        if scope_type == "GLOBAL" and scope_value != "*":
            raise ValueError("global runtime kill switch scope must be '*'")
        now = _utc(self._now())
        affected_profiles = 0
        affected_leases = 0
        with self._database.transaction() as connection:
            existing = connection.execute(
                """
                SELECT active FROM runtime_kill_switches
                WHERE scope_type = ? AND scope_value = ?
                """,
                (scope_type, scope_value),
            ).fetchone()
            prior_active = bool(existing["active"]) if existing is not None else False
            state_changed = prior_active != active
            connection.execute(
                """
                INSERT INTO runtime_kill_switches(scope_type, scope_value, active, actor, reason, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope_type, scope_value) DO UPDATE SET
                    active = excluded.active, actor = excluded.actor,
                    reason = excluded.reason, updated_at = excluded.updated_at
                """,
                (scope_type, scope_value, int(active), actor, reason, now),
            )
            if state_changed and active and scope_type != "DATASET":
                for profile_row in connection.execute(
                    "SELECT * FROM runtime_profiles"
                ).fetchall():
                    profile = json.loads(profile_row["payload_json"])
                    if self._profile_scope_matches(scope_type, scope_value, profile):
                        connection.execute(
                            """
                            UPDATE runtime_profiles
                            SET policy_epoch = policy_epoch + 1, updated_at = ?
                            WHERE profile_id = ? AND profile_version = ?
                            """,
                            (now, profile_row["profile_id"], profile_row["profile_version"]),
                        )
                        affected_profiles += 1
            if state_changed and active:
                for lease in connection.execute(
                    "SELECT * FROM runtime_leases WHERE state = 'ACTIVE'"
                ).fetchall():
                    if self._scope_matches(scope_type, scope_value, lease):
                        connection.execute(
                            """
                            UPDATE runtime_leases
                            SET state = 'DISABLED', revoked_reason = ?, updated_at = ?
                            WHERE lease_id = ? AND state = 'ACTIVE'
                            """,
                            (reason, now, lease["lease_id"]),
                        )
                        affected_leases += 1
            self._audit(
                connection,
                actor=actor,
                action="RUNTIME_KILL_SWITCH_ENABLED" if active else "RUNTIME_KILL_SWITCH_DISABLED",
                resource_id=f"{scope_type}:{scope_value}",
                payload={
                    "active": active,
                    "affectedLeases": affected_leases,
                    "affectedProfiles": affected_profiles,
                    "reason": reason,
                    "stateChanged": state_changed,
                },
                now=now,
            )
        return {
            "scopeType": scope_type,
            "scopeValue": scope_value,
            "active": active,
            "reason": reason,
        }

    def _profile(self, profile_id: str, profile_version: str):
        with self._database.connection() as connection:
            row = self._profile_in(connection, profile_id, profile_version)
        return row, json.loads(row["payload_json"])

    @staticmethod
    def _profile_in(connection: Any, profile_id: str, profile_version: str):
        row = connection.execute(
            "SELECT * FROM runtime_profiles WHERE profile_id = ? AND profile_version = ?",
            (profile_id, profile_version),
        ).fetchone()
        if row is None:
            raise DomainError(
                "RUNTIME_PROFILE_NOT_FOUND", "Runtime profile does not exist", profile_id
            )
        return row

    def _assert_request(self, profile: dict[str, Any], **request: Any) -> None:
        comparisons = (
            ("workload_id", "workloadId", "RUNTIME_WORKLOAD_MISMATCH"),
            ("repo", "repo", "RUNTIME_REPO_MISMATCH"),
            ("environment", "environment", "RUNTIME_ENVIRONMENT_MISMATCH"),
            ("mechanism", "mechanism", "RUNTIME_MECHANISM_MISMATCH"),
        )
        for request_key, profile_key, code in comparisons:
            if request[request_key] != profile[profile_key]:
                raise DomainError(
                    code,
                    "Runtime lease request does not match its profile",
                    str(profile["profileId"]),
                )
        if request["identity"] != profile["workloadIdentity"]:
            raise DomainError(
                "RUNTIME_IDENTITY_MISMATCH",
                "Workload identity does not match the profile",
                str(profile["profileId"]),
            )
        if not _is_sha256(request["artifact_digest"]):
            raise DomainError(
                "RUNTIME_ARTIFACT_INVALID",
                "Runtime lease requires a lowercase SHA-256 artifact identity",
                str(profile["profileId"]),
            )
        requested_scope = set(request["datasets"])
        if not requested_scope or not requested_scope <= set(profile["allowedDatasets"]):
            raise DomainError(
                "RUNTIME_DATASET_SCOPE_VIOLATION",
                "Runtime lease requested datasets outside profile scope",
                str(profile["profileId"]),
            )
        ttl = request["ttl_seconds"]
        if not isinstance(ttl, int) or isinstance(ttl, bool) or ttl < 1 or ttl > 3600:
            raise DomainError(
                "RUNTIME_LEASE_TTL_INVALID",
                "Runtime lease TTL must be between 1 and 3600 seconds",
                str(profile["profileId"]),
            )
        if not request["actor"]:
            raise DomainError(
                "RUNTIME_ACTOR_INVALID",
                "Runtime lease issuance requires an audited actor",
                str(profile["profileId"]),
            )

    @staticmethod
    def _assert_lease_context(row: Any, **context: Any) -> None:
        comparisons = (
            ("identity", "workload_identity", "RUNTIME_IDENTITY_MISMATCH"),
            ("artifact_digest", "artifact_digest", "RUNTIME_ARTIFACT_MISMATCH"),
            ("environment", "environment", "RUNTIME_ENVIRONMENT_MISMATCH"),
            ("mechanism", "mechanism", "RUNTIME_MECHANISM_MISMATCH"),
            ("window_id", "window_id", "RUNTIME_WINDOW_MISMATCH"),
        )
        for context_key, row_key, code in comparisons:
            if context[context_key] != row[row_key]:
                raise DomainError(code, "Runtime lease context mismatch", row["lease_id"])
        requested = set(context["datasets"])
        if not requested or not requested <= set(json.loads(row["datasets_json"])):
            raise DomainError(
                "RUNTIME_DATASET_SCOPE_VIOLATION",
                "Runtime lease validation requested datasets outside lease scope",
                row["lease_id"],
            )

    def _assert_collection_enabled(
        self,
        profile: dict[str, Any],
        *,
        datasets: tuple[str, ...],
        connection: Any | None = None,
    ) -> None:
        expected = {
            ("GLOBAL", "*"),
            ("ENVIRONMENT", str(profile["environment"])),
            ("WORKLOAD", str(profile["workloadId"])),
            ("MECHANISM", str(profile["mechanism"])),
            ("PROFILE", f"{profile['profileId']}@{profile['profileVersion']}"),
            *(("DATASET", dataset) for dataset in datasets),
        }
        if connection is None:
            with self._database.connection() as policy_connection:
                switches = policy_connection.execute(
                    "SELECT scope_type, scope_value FROM runtime_kill_switches WHERE active = 1"
                ).fetchall()
        else:
            switches = connection.execute(
                "SELECT scope_type, scope_value FROM runtime_kill_switches WHERE active = 1"
            ).fetchall()
        active = sorted(
            f"{row['scope_type']}:{row['scope_value']}"
            for row in switches
            if (row["scope_type"], row["scope_value"]) in expected
        )
        if active:
            raise DomainError(
                "RUNTIME_COLLECTION_DISABLED",
                "Runtime collection is disabled by policy",
                str(profile["profileId"]),
                {"switches": active},
            )

    @staticmethod
    def _scope_matches(scope_type: str, scope_value: str, lease: Any) -> bool:
        return {
            "GLOBAL": scope_value == "*",
            "ENVIRONMENT": lease["environment"] == scope_value,
            "WORKLOAD": lease["workload_id"] == scope_value,
            "MECHANISM": lease["mechanism"] == scope_value,
            "PROFILE": f"{lease['profile_id']}@{lease['profile_version']}" == scope_value,
            "DATASET": scope_value in set(json.loads(lease["datasets_json"])),
        }[scope_type]

    @staticmethod
    def _profile_scope_matches(
        scope_type: str, scope_value: str, profile: dict[str, Any]
    ) -> bool:
        return {
            "GLOBAL": scope_value == "*",
            "ENVIRONMENT": profile["environment"] == scope_value,
            "WORKLOAD": profile["workloadId"] == scope_value,
            "MECHANISM": profile["mechanism"] == scope_value,
            "PROFILE": f"{profile['profileId']}@{profile['profileVersion']}" == scope_value,
            "DATASET": scope_value in set(profile["allowedDatasets"]),
        }[scope_type]

    def _expire_in(self, connection: Any, lease_id: str, now: str) -> None:
        updated = connection.execute(
            """
            UPDATE runtime_leases SET state = 'EXPIRED', updated_at = ?
            WHERE lease_id = ? AND state = 'ACTIVE'
            """,
            (now, lease_id),
        )
        if updated.rowcount:
            self._audit(
                connection,
                actor="runtime-policy",
                action="RUNTIME_LEASE_EXPIRED",
                resource_id=lease_id,
                payload={"expiredAt": now},
                now=now,
            )

    def _verify_token(self, row: Any, token: str) -> None:
        try:
            encoded, supplied_signature = token.split(".", 1)
        except ValueError as error:
            raise DomainError(
                "RUNTIME_LEASE_SIGNATURE_INVALID",
                "Runtime lease signature is invalid",
                row["lease_id"],
            ) from error
        expected_signature = hmac.new(
            self._secret, encoded.encode(), hashlib.sha256
        ).hexdigest()
        token_digest = hashlib.sha256(token.encode()).hexdigest()
        if not hmac.compare_digest(expected_signature, supplied_signature) or not hmac.compare_digest(
            row["token_digest"], token_digest
        ):
            raise DomainError(
                "RUNTIME_LEASE_SIGNATURE_INVALID",
                "Runtime lease signature is invalid",
                row["lease_id"],
            )
        try:
            padding = "=" * (-len(encoded) % 4)
            claims = json.loads(base64.urlsafe_b64decode(encoded + padding))
        except (ValueError, json.JSONDecodeError) as error:
            raise DomainError(
                "RUNTIME_LEASE_SIGNATURE_INVALID",
                "Runtime lease claims are invalid",
                row["lease_id"],
            ) from error
        if _canonical(claims) != row["payload_json"]:
            raise DomainError(
                "RUNTIME_LEASE_SIGNATURE_INVALID",
                "Runtime lease claims do not match durable authority",
                row["lease_id"],
            )

    def _sign(self, claims: dict[str, object]) -> str:
        encoded = base64.urlsafe_b64encode(_canonical(claims).encode()).decode().rstrip("=")
        signature = hmac.new(self._secret, encoded.encode(), hashlib.sha256).hexdigest()
        return f"{encoded}.{signature}"

    def _now(self) -> datetime:
        value = self._clock.now()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("runtime policy clock must return a timezone-aware datetime")
        return value.astimezone(UTC)

    @staticmethod
    def _validate_profile(profile: dict[str, object]) -> None:
        if set(profile) != _PROFILE_REQUIRED:
            raise ValueError("runtime profile must match the closed profile contract")
        if profile.get("schemaVersion") != "1.0.0":
            raise ValueError("unsupported runtime profile schema version")
        if profile.get("mechanism") not in _MECHANISMS:
            raise ValueError("unsupported runtime profile mechanism")
        if profile.get("environmentClass") not in _ENVIRONMENT_CLASSES:
            raise ValueError("unsupported runtime environment class")
        if profile.get("artifactIdentityStrategy") != "DIGEST":
            raise ValueError("runtime profile must use exact digest artifact identity")
        for key in (
            "profileId",
            "profileVersion",
            "owner",
            "workloadId",
            "workloadIdentity",
            "repo",
            "environment",
        ):
            if not isinstance(profile.get(key), str) or not profile[key]:
                raise ValueError(f"runtime profile {key} must be non-empty")
        if _SEMVER.fullmatch(str(profile["profileVersion"])) is None:
            raise ValueError("runtime profile profileVersion must be semantic version X.Y.Z")
        if not _is_sha256(profile.get("packageDigest")):
            raise ValueError("runtime profile packageDigest must be an exact SHA-256 identity")
        if profile.get("installMode") not in _INSTALL_MODES:
            raise ValueError("unsupported runtime profile installMode")
        if profile.get("deploymentCriticality") not in {"REQUIRED", "OPTIONAL"}:
            raise ValueError("unsupported runtime profile deploymentCriticality")
        framework = profile.get("framework")
        if not isinstance(framework, dict) or set(framework) != {"name", "versionRange"} or not all(
            isinstance(framework.get(key), str) and framework[key]
            for key in ("name", "versionRange")
        ):
            raise ValueError("runtime profile framework must contain name and versionRange")
        RuntimePolicyService._string_list(profile, "allowedDatasets", allow_empty=False)
        RuntimePolicyService._string_list(profile, "allowedAttributes", allow_empty=True)
        RuntimePolicyService._string_list(profile, "parserContracts", allow_empty=True)
        granularities = profile.get("permittedGranularity")
        if (
            not isinstance(granularities, list)
            or not granularities
            or len(granularities) != len(set(granularities))
            or not set(granularities) <= _GRANULARITIES
        ):
            raise ValueError("runtime profile permittedGranularity is invalid")
        RuntimePolicyService._budget(
            profile,
            "bufferBudget",
            positive=("maxRecords", "maxBytes", "drainTimeoutSeconds"),
            non_negative=("enqueueTimeoutMs", "maxRetries"),
        )
        overhead = profile.get("overheadBudget")
        if not isinstance(overhead, dict) or set(overhead) != {
            "maxCpuPercent",
            "maxMemoryBytes",
            "maxP95EnqueueMillis",
        }:
            raise ValueError("runtime profile overheadBudget is invalid")
        cpu = overhead["maxCpuPercent"]
        if not isinstance(cpu, (int, float)) or isinstance(cpu, bool) or cpu <= 0 or cpu > 100:
            raise ValueError("runtime profile overheadBudget.maxCpuPercent is invalid")
        memory = overhead["maxMemoryBytes"]
        if not isinstance(memory, int) or isinstance(memory, bool) or memory < 1:
            raise ValueError("runtime profile overheadBudget.maxMemoryBytes is invalid")
        latency = overhead["maxP95EnqueueMillis"]
        if not isinstance(latency, (int, float)) or isinstance(latency, bool) or latency < 0:
            raise ValueError("runtime profile overheadBudget.maxP95EnqueueMillis is invalid")
        canary = profile.get("canaryPercent")
        if not isinstance(canary, int) or isinstance(canary, bool) or not 0 <= canary <= 100:
            raise ValueError("runtime profile canaryPercent is invalid")

    @staticmethod
    def _string_list(profile: dict[str, object], key: str, *, allow_empty: bool) -> None:
        values = profile.get(key)
        if (
            not isinstance(values, list)
            or (not allow_empty and not values)
            or len(values) != len(set(values))
            or not all(isinstance(value, str) and value for value in values)
        ):
            raise ValueError(f"runtime profile {key} is invalid")

    @staticmethod
    def _budget(
        profile: dict[str, object],
        key: str,
        *,
        positive: tuple[str, ...],
        non_negative: tuple[str, ...],
    ) -> None:
        budget = profile.get(key)
        expected = set(positive) | set(non_negative)
        if not isinstance(budget, dict) or set(budget) != expected:
            raise ValueError(f"runtime profile {key} is invalid")
        for field in positive:
            value = budget[field]
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"runtime profile {key}.{field} is invalid")
        for field in non_negative:
            value = budget[field]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"runtime profile {key}.{field} is invalid")

    @staticmethod
    def _audit(
        connection: Any,
        *,
        actor: str,
        action: str,
        resource_id: str,
        payload: dict[str, object],
        now: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_events(
                audit_id, actor, action, resource_type, resource_id,
                correlation_id, payload_json, created_at
            ) VALUES (?, ?, ?, 'runtime_policy', ?, ?, ?, ?)
            """,
            (
                f"audit-{uuid.uuid4().hex}",
                actor,
                action,
                resource_id,
                resource_id,
                _canonical(payload),
                now,
            ),
        )
