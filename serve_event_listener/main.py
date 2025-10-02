"""CLI entry point for the Kubernetes Event Listener service.

Configures colored logging, parses minimal CLI flags, and starts the
EventListener. Intended to be run with `python -m serve_event_listener.main`
(or as a Docker ENTRYPOINT).

Environment:
    DEBUG: truthy string enables DEBUG logging; otherwise INFO. Default DEBUG.
"""

import argparse
import logging
import os
import sys

from colorlog import ColoredFormatter

from serve_event_listener.event_listener import EventListener
from serve_event_listener.http_client import make_session
from serve_event_listener.probing import AppAvailabilityProbe

# Configure the logger
formatter = ColoredFormatter(
    "%(log_color)s%(asctime)s - %(levelname)s - %(module)s: %(message)s%(reset)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    log_colors={
        "DEBUG": "blue",
        "INFO": "green",
        "WARNING": "yellow",
        "ERROR": "red",
        "CRITICAL": "red,bg_white",
    },
)

DEBUG = os.getenv("DEBUG", default="True").lower() in ("true", "1", "t")
# Set up logging configuration with the ColoredFormatter
level = logging.DEBUG if DEBUG else logging.INFO
logging.basicConfig(
    level=level, format="%(message)s", handlers=[logging.StreamHandler()]
)
handler = logging.StreamHandler()
handler.setFormatter(formatter)
logging.getLogger().handlers = [handler]
logger = logging.getLogger(__name__)


def parse_args():
    """Parse command-line options for the event listener.

    Returns:
        argparse.Namespace: Parsed arguments containing:
            - namespace (str): Kubernetes namespace to watch.
            - label_selector (str): Label selector for filtering pods.
    """
    parser = argparse.ArgumentParser(description="Kubernetes Event Listener")
    parser.add_argument(
        "--namespace",
        help="Kubernetes namespace to watch",
        default="default",
    )
    parser.add_argument(
        "--label-selector",
        help="Label selector for filtering pods",
        default="type=app",
    )
    parser.add_argument(
        "--mode",
        choices=["normal", "diagnostics", "probetest"],
        default="normal",
        help="run mode (default: normal)",
    )
    parser.add_argument(
        "--probe-url",
        dest="probe_url",
        help="url to probe when --mode=probetest",
    )
    # optional ergonomics for probetest; safe defaults
    parser.add_argument(
        "--probe-insecure",
        action="store_true",
        help="skip TLS verification during --mode=probetest",
    )
    parser.add_argument(
        "--probe-connect-timeout",
        type=float,
        default=3.05,
        help="connect timeout (s) for --mode=probetest",
    )
    parser.add_argument(
        "--probe-read-timeout",
        type=float,
        default=10.0,
        help="read timeout (s) for --mode=probetest",
    )
    return parser.parse_args()


def _env(name: str, *, secret: bool = False, default: str | None = None) -> str:
    """fetch env var for diagnostics; redact if secret."""
    val = os.getenv(name, default)
    if secret:
        return "<set>" if val else "<unset>"
    return val if val is not None else "<unset>"


def _print_diagnostics(args) -> None:
    """dump effective config and exit."""
    # import here so diagnostics works even if optional modules change
    try:
        import serve_event_listener.status_queue as sq

        probe_cfg = {
            "APP_PROBE_STATUSES": ",".join(sorted(sq.APP_PROBE_STATUSES))
            or "<disabled>",
            "APP_PROBE_APPS": ",".join(sorted(sq.APP_PROBE_APPS)) or "<none>",
            "RUNNING_PROBE_WINDOW": getattr(sq, "RUNNING_PROBE_WINDOW", "<n/a>"),
            "RUNNING_PROBE_INTERVAL": getattr(sq, "RUNNING_PROBE_INTERVAL", "<n/a>"),
            "DELETED_PROBE_WINDOW": getattr(sq, "DELETED_PROBE_WINDOW", "<n/a>"),
            "DELETED_PROBE_INTERVAL": getattr(sq, "DELETED_PROBE_INTERVAL", "<n/a>"),
            "NXDOMAIN_CONFIRMATION_COUNT": getattr(
                sq, "NXDOMAIN_CONFIRMATION_COUNT", "<n/a>"
            ),
        }
    except Exception:
        probe_cfg = {
            "APP_PROBE_STATUSES": "<unavailable>",
            "APP_PROBE_APPS": "<unavailable>",
            "RUNNING_PROBE_WINDOW": "<unavailable>",
            "RUNNING_PROBE_INTERVAL": "<unavailable>",
            "DELETED_PROBE_WINDOW": "<unavailable>",
            "DELETED_PROBE_INTERVAL": "<unavailable>",
            "NXDOMAIN_CONFIRMATION_COUNT": "<unavailable>",
        }

    logger.info("\n===== diagnostics =====")
    logger.info("mode: diagnostics")
    logger.info("debug: %s", DEBUG)
    logger.info("--- cli ---")
    logger.info("namespace: %s", args.namespace)
    logger.info("label_selector: %s", args.label_selector)
    logger.info("probe_url: %s", args.probe_url or "<none>")
    logger.info("--- env ---")
    logger.info("KUBECONFIG: %s", _env("KUBECONFIG"))
    logger.info("BASE_URL: %s", _env("BASE_URL"))
    logger.info("TOKEN_API_ENDPOINT: %s", _env("TOKEN_API_ENDPOINT"))
    logger.info("APP_STATUS_API_ENDPOINT: %s", _env("APP_STATUS_API_ENDPOINT"))
    logger.info("USERNAME: %s", _env("USERNAME"))
    logger.info("PASSWORD: %s", _env("PASSWORD", secret=True))
    logger.info("APP_URL_DNS_MODE: %s", _env("APP_URL_DNS_MODE", default="short"))
    logger.info("APP_URL_PORT: %s", _env("APP_URL_PORT", default="80"))
    logger.info("--- probing ---")
    for k, v in probe_cfg.items():
        logger.info("%s: %s", k, v)
    logger.info("===== end diagnostics =====\n")


def _run_probetest(args) -> None:
    """single-shot probe and exit."""
    if not args.probe_url:
        logger.error("--mode=probetest requires --probe-url <url>")
        sys.exit(2)

    # small, fast session (no heavy retrying needed here)
    session = make_session(total_retries=1)

    prober = AppAvailabilityProbe(
        session,
        verify_tls=not args.probe_insecure,
        timeout=(args.probe_connect_timeout, args.probe_read_timeout),
        backoff_seconds=(0.5,),  # quick single retry if your prober uses it
    )

    logger.info("running probe test for: %s", args.probe_url)
    pr = prober.probe_url(args.probe_url)

    # human-friendly summary
    logger.info(
        "probe result: status=%s http=%s note=%s url=%s",
        pr.status,  # "Running" | "Unknown" | "NotFound"
        pr.port80_status,  # e.g. 200, 301, None
        pr.note or "",
        args.probe_url,
    )

    # exit code mapping so it’s scriptable if needed
    # 0 = Running, 3 = NotFound, 4 = Unknown/other
    exit_code = 0 if pr.status == "Running" else (3 if pr.status == "NotFound" else 4)
    sys.exit(exit_code)


def run(namespace: str, label_selector: str) -> None:
    """Start the event listener service.

    Args:
        namespace (str): Kubernetes namespace to watch.
        label_selector (str): Label selector used to filter pods.
    """

    start_message = (
        "\n\n\t{}\n\t"
        "Starting Kubernetes Event Listener \n\t"
        "Namespace: {}\n\t"
        "Label Selector: {}\n\t"
        "debug: {}\n\t"
        "{}\n"
    )
    logger.info(
        start_message.format("#" * 40, namespace, label_selector, DEBUG, "#" * 40)
    )

    event_listener = EventListener(namespace, label_selector)
    event_listener.setup()
    event_listener.listen()


def main():
    """Module entry point: parse flags and run the service."""
    args = parse_args()

    if args.mode == "diagnostics":
        _print_diagnostics(args)
        return

    if args.mode == "probetest":
        _run_probetest(args)
        return

    # normal mode
    run(args.namespace, args.label_selector)


if __name__ == "__main__":
    main()
