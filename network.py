"""Bounded public HTTPS transport with DNS pinning and redirect revalidation."""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from core import now


class PolicyError(ValueError):
    pass


class BudgetError(RuntimeError):
    pass


def public_addresses(host: str) -> list[str]:
    if host.casefold() in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise PolicyError("Local network host blocked")
    addresses = sorted({entry[4][0] for entry in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise PolicyError("Non-public network address blocked")
    return addresses


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str, timeout: float):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        plain = socket.create_connection((self.address, 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(plain, server_hostname=self.host)
        except BaseException:
            plain.close()
            raise


@dataclass
class Response:
    url: str
    status: int
    body: bytes
    retrieved_at: str
    content_type: str
    elapsed_ms: int


class Client:
    def __init__(self, max_requests: int = 2000, seconds: float = 2700, timeout: float = 15, max_bytes: int = 2_000_000):
        self.max_requests = max_requests
        self.deadline = time.monotonic() + seconds
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.requests = 0
        self.bytes = 0
        self.guard = threading.Lock()
        self.host_locks: dict[str, threading.Lock] = {}
        self.last_start: dict[str, float] = {}

    def get(self, url: str, *, accept: str = "application/json") -> Response:
        for attempt in range(2):
            try:
                return self._get(url, accept=accept)
            except (OSError, http.client.HTTPException):
                if attempt or time.monotonic() >= self.deadline:
                    raise
        raise RuntimeError("Unreachable retry state")

    def _get(self, url: str, *, accept: str = "application/json") -> Response:
        for _ in range(6):
            parsed = urlsplit(url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise PolicyError("Only public HTTPS URLs without credentials are allowed")
            if parsed.port not in {None, 443}:
                raise PolicyError("Nonstandard port blocked")
            host = parsed.hostname.encode("idna").decode("ascii").lower()
            addresses = public_addresses(host)
            with self.guard:
                lock = self.host_locks.setdefault(host, threading.Lock())
            with lock:
                delay = max(0, self.last_start.get(host, 0) + .35 - time.monotonic())
                if delay:
                    time.sleep(delay)
                with self.guard:
                    remaining = self.deadline - time.monotonic()
                    if remaining <= 0 or self.requests >= self.max_requests:
                        raise BudgetError("Declared run budget exhausted")
                    self.requests += 1
                    self.last_start[host] = time.monotonic()
            began = time.monotonic()
            request_deadline = min(self.deadline, began + self.timeout)
            connection = PinnedHTTPS(host, addresses[0], min(self.timeout, remaining))
            try:
                    path = parsed.path or "/"
                    if parsed.query:
                        path += "?" + parsed.query
                    connection.request("GET", path, headers={"Accept": accept,
                        "User-Agent": "FjordResearch/0.1 (evidence-first company research; github.com/nqtexd)",
                        "Accept-Encoding": "identity"})
                    response = connection.getresponse()
                    if response.status in {301, 302, 303, 307, 308}:
                        location = response.getheader("Location")
                        if not location:
                            raise PolicyError("Redirect missing a destination")
                        url = urljoin(url, location)
                        continue
                    declared = response.getheader("Content-Length")
                    if declared and int(declared) > self.max_bytes:
                        raise PolicyError("Source exceeds response size limit")
                    chunks = []
                    size = 0
                    while True:
                        remaining_read = request_deadline - time.monotonic()
                        if remaining_read <= 0:
                            raise TimeoutError("Source exceeded request time limit")
                        response.fp.raw._sock.settimeout(remaining_read)
                        chunk = response.read1(min(65536, self.max_bytes + 1 - size))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        size += len(chunk)
                        if size > self.max_bytes:
                            raise PolicyError("Source exceeds response size limit")
                    body = b"".join(chunks)
                    if len(body) > self.max_bytes:
                        raise PolicyError("Source exceeds response size limit")
                    with self.guard:
                        self.bytes += len(body)
                    return Response(url, response.status, body, now(), response.getheader("Content-Type", ""),
                                    int((time.monotonic() - began) * 1000))
            finally:
                connection.close()
        raise PolicyError("Redirect chain exceeds limit")
