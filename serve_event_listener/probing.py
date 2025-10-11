"""Curl-like availability checks of user apps."""

import logging
import socket
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import requests

from serve_event_listener.el_types import ProbeStatus
from serve_event_listener.http_client import get as http_get

logger = logging.getLogger(__name__)


@dataclass
class ProbeResult:
    """Outcome of an availability probe."""

    status: ProbeStatus  # "Running" | "Unknown" | "NotFound"
    port80_status: Optional[int] = None
    note: str = ""  # short diagnostic


class AppAvailabilityProbe:
    """
    Curl-like availability check using only a port-80 URL.

    Classification:
      - NotFound: DNS cannot resolve the host
      - Running:  HTTP status 2xx or 3xx
      - Unknown:  DNS resolves, but no 2xx/3xx (e.g., connection refused, timeout, 4xx/5xx)
                  (treating 4xx/5xx as 'Unknown' surfaces misconfig/errors; adjust if desired)
    """

    def __init__(
        self,
        session: requests.Session,
        *,
        timeout: tuple[float, float] = (1.5, 3.0),
        backoff_seconds: tuple[float, ...] = (0.5, 1.0),
    ) -> None:
        self.session = session
        self.timeout = timeout
        self.backoff_seconds = backoff_seconds

    def _dns_resolves(self, url: str) -> bool:
        host = urlparse(url).hostname
        if not host:
            return False
        try:
            socket.getaddrinfo(host, None)
            return True
        except socket.gaierror:
            return False

    def probe_url(self, port80_url: str, headers: dict | None = None) -> ProbeResult:
        """Probe a single HTTP URL and classify availability."""
        # NotFound — DNS cannot resolve
        if not self._dns_resolves(port80_url):
            logger.debug(
                "Skipping URL probing because DNS resolution failed for url %s",
                port80_url,
            )
            return ProbeResult(status="NotFound", note="DNS resolution failed")

        # Try GET; http_get returns None on network errors/timeouts
        logger.debug("Probing URL: %s", port80_url)

        resp = http_get(
            self.session,
            port80_url,
            headers=headers,
            timeout=self.timeout,
            backoff_seconds=self.backoff_seconds,
            allow_redirects=True,
        )
        code = resp.status_code if resp is not None else None

        # Running — server responds OK/redirect
        if code is not None and 200 <= code < 400:
            return ProbeResult(
                status="Running", port80_status=code, note="HTTP 2xx/3xx"
            )

        # Unknown — DNS ok but no success (refused/timeout/4xx/5xx)
        return ProbeResult(
            status="Unknown",
            port80_status=code,
            note="DNS ok; no 2xx/3xx (refused/timeout/4xx/5xx)",
        )
