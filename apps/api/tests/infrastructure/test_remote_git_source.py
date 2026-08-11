from __future__ import annotations

import importlib
import socket

import pytest

from lineage_api.infrastructure.remote_git_source import (
    RemoteGitSourceError,
    resolve_public_git_addresses,
)


def test_remote_git_repository_source_is_available() -> None:
    module = importlib.import_module("lineage_api.infrastructure.remote_git_source")

    assert module.RemoteGitRepositorySource is not None


def _address_info(address: str) -> tuple[object, ...]:
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    sockaddr: tuple[object, ...]
    if family == socket.AF_INET6:
        sockaddr = (address, 0, 0, 0)
    else:
        sockaddr = (address, 0)
    return family, socket.SOCK_STREAM, 6, "", sockaddr


def _resolver_for(*addresses: str):
    answers = [_address_info(address) for address in addresses]

    def resolver(*args: object, **kwargs: object) -> list[tuple[object, ...]]:
        return answers

    return resolver


def test_resolves_public_ipv4_and_ipv6_with_deterministic_deduplication() -> None:
    addresses = ("2606:4700:4700::1111", "93.184.216.34", "8.8.8.8")
    first = resolve_public_git_addresses(
        "public.example",
        resolver=_resolver_for(addresses[0], addresses[1], addresses[0], addresses[2]),
    )
    second = resolve_public_git_addresses(
        "public.example",
        resolver=_resolver_for(addresses[2], addresses[0], addresses[1]),
    )

    assert first == second == (
        "8.8.8.8",
        "93.184.216.34",
        "2606:4700:4700::1111",
    )


@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.1",
        "127.0.0.1",
        "169.254.1.1",
        "224.0.0.1",
        "240.0.0.1",
        "0.0.0.0",
        "::1",
        "fe80::1",
        "ff02::1",
        "::",
    ],
)
def test_rejects_non_public_address_classes(address: str) -> None:
    with pytest.raises(RemoteGitSourceError, match="^remote Git host resolution rejected$"):
        resolve_public_git_addresses("public.example", resolver=_resolver_for(address))


def test_rejects_mixed_public_and_private_answers() -> None:
    with pytest.raises(RemoteGitSourceError, match="^remote Git host resolution rejected$"):
        resolve_public_git_addresses(
            "public.example",
            resolver=_resolver_for("8.8.8.8", "10.0.0.1"),
        )


@pytest.mark.parametrize(
    "answers",
    [
        [],
        [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("not-an-address", 0))],
        [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ())],
    ],
)
def test_rejects_empty_or_malformed_resolver_answers(
    answers: list[tuple[object, ...]],
) -> None:
    def resolver(*args: object, **kwargs: object) -> list[tuple[object, ...]]:
        return answers

    with pytest.raises(RemoteGitSourceError, match="^remote Git host resolution rejected$"):
        resolve_public_git_addresses("public.example", resolver=resolver)


def test_rejects_resolver_exceptions_without_leaking_details() -> None:
    def resolver(*args: object, **kwargs: object) -> list[tuple[object, ...]]:
        raise OSError("sensitive.example resolved to 10.0.0.1")

    with pytest.raises(RemoteGitSourceError) as caught:
        resolve_public_git_addresses("sensitive.example", resolver=resolver)

    assert str(caught.value) == "remote Git host resolution rejected"
    assert len(str(caught.value)) <= 64
    assert "sensitive.example" not in str(caught.value)
    assert "10.0.0.1" not in str(caught.value)


def test_rejects_more_than_sixteen_resolver_answers() -> None:
    addresses = tuple(f"8.8.8.{last_octet}" for last_octet in range(1, 18))

    with pytest.raises(RemoteGitSourceError, match="^remote Git host resolution rejected$"):
        resolve_public_git_addresses("public.example", resolver=_resolver_for(*addresses))
