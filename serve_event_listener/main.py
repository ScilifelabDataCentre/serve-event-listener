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

from colorlog import ColoredFormatter

from serve_event_listener.event_listener import EventListener

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
if DEBUG:
    level = logging.DEBUG
else:
    level = logging.INFO

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
    return parser.parse_args()


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
    run(args.namespace, args.label_selector)


if __name__ == "__main__":
    main()
