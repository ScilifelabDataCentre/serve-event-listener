from __future__ import annotations

import os
from typing import Optional
from urllib.parse import urlunparse

from serve_event_listener.el_types import StatusRecord

# for non-cluster, development / testing
_HOST_GATEWAY = "host.docker.internal"
_PF = os.getenv("PROBE_PF")


def _host_for(service: str, namespace: str) -> str:
    """Build host according to DNS mode env settings."""
    mode = (os.getenv("APP_URL_DNS_MODE", "short") or "short").lower()
    suffix = os.getenv("APP_URL_DNS_SUFFIX")
    if mode == "fqdn":
        return f"{service}.{namespace}.svc.cluster.local"
    if suffix:
        return f"{service}.{namespace}.{suffix}"
    # default short form: service.namespace
    return f"{service}.{namespace}"


def _port() -> str:
    return os.getenv("APP_URL_PORT", "80")


def _scheme() -> str:
    return os.getenv("APP_URL_SCHEME", "http")


def _pf_port_for_release(release: str) -> Optional[str]:
    if not _PF:
        return None
    s = _PF.strip()
    if s.isdigit():
        return s
    # parse mapping: rel:port,rel2:port2
    for part in s.split(","):
        if ":" in part:
            rel, port = part.split(":", 1)
            rel, port = rel.strip(), port.strip()
            if rel == release and port.isdigit():
                return port
    return None


def resolve_app_url(
    rec: StatusRecord, *, fallback_namespace: Optional[str] = None
) -> Optional[str]:
    """
    Return a cluster-internal HTTP URL for the given StatusRecord, or None if unknown.

    Currently supports:
      - app-type == 'shiny-proxy':
          service: <release>-<SHINYPROXY_SERVICE_SUFFIX>
          host:    per DNS mode (short/fqdn/custom suffix)
          path:    <SHINYPROXY_PATH_PREFIX>/<release>/
    """
    app_type = (rec.get("app-type") or "").lower()
    assert app_type is not None, "app_type should be set"
    if not app_type:
        return None

    release = rec.get("release")
    assert release is not None, "release should be set"
    if not release:
        return None

    namespace = rec.get("namespace") or fallback_namespace or "default"

    if app_type == "shiny-proxy":
        pf_port = _pf_port_for_release(release)
        if pf_port:
            # bypass proxy: talk to the port-forward on the host
            return f"http://{_HOST_GATEWAY}:{pf_port}/app/{release}/"

        # apply the normal ingress/cluster logic
        suffix = os.getenv("SHINYPROXY_SERVICE_SUFFIX", "shinyproxyapp")
        path_prefix = os.getenv("SHINYPROXY_PATH_PREFIX", "/app").rstrip("/")
        service = f"{release}-{suffix}"
        host = _host_for(service, namespace)
        path = f"{path_prefix}/{release}/"
        netloc = f"{host}:{_port()}"
        return urlunparse((_scheme(), netloc, path, "", "", ""))

    # Other app types are not yet supported
    return None
