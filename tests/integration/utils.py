import unittest
from datetime import datetime, timedelta, timezone

from kubernetes import client, config


def iso_now(offset_sec: float = 0.0) -> str:
    """Return current UTC time as RFC3339/ISO string with Z suffix."""
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_sec)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )


def iso_at(dt):
    """Return a datetime as a RFC3339/ISO string with Z suffix."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def build_shiny_proxy_url(release: str, namespace: str) -> str:
    """Builds and returns a shiny proxy URL, using the short-DNS pattern used elsewhere."""
    return f"http://{release}-shinyproxyapp.{namespace}/app/{release}/"


def find_deployment_for_release(apps: client.AppsV1Api, namespace: str, release: str):
    """
    Return a Deployment whose *pod template* labels match the given release.
    We check both 'release' and 'app.kubernetes.io/instance', and also allow
    a simple name prefix fallback (e.g., '<release>-shinyproxy...' patterns).
    """
    deps = apps.list_namespaced_deployment(
        namespace=namespace, timeout_seconds=20
    ).items
    for d in deps:
        md = d.metadata or {}
        tmd = (d.spec or {}).template.metadata if d.spec else None
        md_labels = md.labels or {}
        tpl_labels = (tmd.labels or {}) if tmd else {}

        rels = {
            md_labels.get("release"),
            tpl_labels.get("release"),
            md_labels.get("app.kubernetes.io/instance"),
            tpl_labels.get("app.kubernetes.io/instance"),
        }

        if release in rels:
            return d

        # fallback: some setups name the deployment with the release prefix
        if (md.name or "").startswith(release):
            return d

    return None


def kube_load_config(*, allow_skip: bool = True) -> str:
    """
    Load Kubernetes client configuration for tests.

    Order of attempts:
      1) Respect $KUBECONFIG (single file or colon-separated list).
      2) Fall back to default kubeconfig (~/.kube/config) if present.
      3) Fall back to in-cluster config.

    Args:
        allow_skip: If True, raises unittest.SkipTest when nothing works;
                    otherwise re-raises the original exception.

    Returns:
        A string indicating which mode was loaded: "kubeconfig" or "incluster".

    Raises:
        unittest.SkipTest (when allow_skip=True) or the last Exception if all attempts fail.
    """
    # try kubeconfig (explicit env or default locations)
    try:
        # honors $KUBECONFIG automatically if set; else uses ~/.kube/config
        config.load_kube_config()
        return "kubeconfig"
    except Exception as e_kube:
        # try in-cluster config next
        try:
            config.incluster_config.load_incluster_config()
            return "incluster"
        except Exception as e_in:
            if allow_skip:
                raise unittest.SkipTest(
                    f"Could not load Kubernetes config (kubeconfig and in-cluster failed): "
                    f"{type(e_kube).__name__}: {e_kube!s} | {type(e_in).__name__}: {e_in!s}"
                )
            # fail hard if requested
            raise
