"""HTTP helpers for ingestion jobs when local DNS is unreliable."""

from __future__ import annotations

import contextlib
import socket
import subprocess
from collections.abc import Iterator

import requests

DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)"


def _resolve_via_public_dns(hostname: str) -> str | None:
    try:
        result = subprocess.run(
            ["host", hostname, "8.8.8.8"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return None
    for line in result.stdout.splitlines():
        if " has address " in line:
            return line.rsplit(" ", 1)[-1].strip()
    return None


@contextlib.contextmanager
def public_dns_fallback() -> Iterator[None]:
    """Retry host resolution through Google DNS when the system resolver fails."""
    original_getaddrinfo = socket.getaddrinfo

    def patched_getaddrinfo(
        host: str,
        port: int | str | None,
        family: int = 0,
        type: int = 0,
        proto: int = 0,
        flags: int = 0,
    ):
        try:
            return original_getaddrinfo(host, port, family, type, proto, flags)
        except socket.gaierror:
            ip = _resolve_via_public_dns(host)
            if not ip:
                raise
            return original_getaddrinfo(ip, port, family, type, proto, flags)

    socket.getaddrinfo = patched_getaddrinfo
    try:
        yield
    finally:
        socket.getaddrinfo = original_getaddrinfo


def make_ingest_session(*, user_agent: str = DEFAULT_USER_AGENT) -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})
    return session
