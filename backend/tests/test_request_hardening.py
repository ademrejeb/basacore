"""Unit tests for the address-trust and egress guards.

These run without a database: they cover the two places where a client-supplied
value used to reach a privileged decision (audit IP / rate-limit key) or the
network (tenant domain TLS probe).
"""

from __future__ import annotations

import socket

import pytest
from starlette.requests import Request

from app.core import middleware
from app.core.config import Settings
from app.modules.tenants import service as tenant_service


def _request(peer: str, forwarded: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": headers,
            "client": (peer, 12345),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )


@pytest.fixture
def proxy_settings(monkeypatch: pytest.MonkeyPatch):
    def configure(cidrs: str) -> None:
        monkeypatch.setattr(middleware, "settings", Settings(trusted_proxy_cidrs=cidrs))

    return configure


def test_forwarded_header_is_ignored_without_a_trusted_proxy(
    proxy_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    proxy_settings("")
    request = _request("198.51.100.7", "1.2.3.4")
    assert middleware.resolve_client_ip(request) == "198.51.100.7"


def test_forwarded_header_is_ignored_when_the_peer_is_untrusted(
    proxy_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    proxy_settings("10.0.0.0/8")
    request = _request("198.51.100.7", "1.2.3.4")
    assert middleware.resolve_client_ip(request) == "198.51.100.7"


def test_forwarded_header_uses_the_rightmost_untrusted_hop(proxy_settings) -> None:
    proxy_settings("10.0.0.0/8")
    request = _request("10.1.2.3", "203.0.113.9, 10.9.9.9")
    assert middleware.resolve_client_ip(request) == "203.0.113.9"


def test_forwarded_chain_of_only_proxies_falls_back_to_the_peer(proxy_settings) -> None:
    proxy_settings("10.0.0.0/8")
    request = _request("10.1.2.3", "10.9.9.9")
    assert middleware.resolve_client_ip(request) == "10.1.2.3"


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.0.0.5", "169.254.169.254", "::1"],
)
def test_private_addresses_are_rejected_for_tls_inspection(
    monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    monkeypatch.setattr(
        tenant_service.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(family, socket.SOCK_STREAM, 6, "", (address, 443, 0, 0))],
    )
    assert tenant_service._public_address("internal.example") is None


def test_public_address_is_used_and_connect_is_skipped_when_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tenant_service.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    )
    assert tenant_service._public_address("example.com") == "93.184.216.34"

    monkeypatch.setattr(
        tenant_service.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    )

    def _must_not_connect(*args, **kwargs):  # pragma: no cover - asserted below
        raise AssertionError("the TLS probe must not open a socket to a private address")

    monkeypatch.setattr(tenant_service.socket, "create_connection", _must_not_connect)
    assert tenant_service._tls_expiry("localhost") is None
