from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable


_MAX_RESOLVED_ADDRESSES = 16
_RESOLUTION_REJECTED = "remote Git host resolution rejected"


class RemoteGitSourceError(RuntimeError):
    """Raised when a remote Git source violates source policy."""


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


class RemoteGitRepositorySource:
    """Acquires exact remote Git revisions under bounded source policy."""

    def __init__(self) -> None:
        pass
