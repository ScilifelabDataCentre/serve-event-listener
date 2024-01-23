import argparse
import logging

import requests
from colorlog import ColoredFormatter
from init_stream import init
from kubernetes import watch

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

# Set up logging configuration with the ColoredFormatter
logging.basicConfig(
    level=logging.DEBUG, format="%(message)s", handlers=[logging.StreamHandler()]
)
handler = logging.StreamHandler()
handler.setFormatter(formatter)
logging.getLogger().handlers = [handler]

logger = logging.getLogger(__name__)


def parse_args():
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


if __name__ == "__main__":
    args = parse_args()

    logger.info(
        "Starting Kubernetes Event Listener with namespace: %s, label selector: %s",
        args.namespace,
        args.label_selector,
    )

    init(args.namespace, args.label_selector)
