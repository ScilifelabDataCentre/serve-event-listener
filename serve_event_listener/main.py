import argparse
import logging
import os

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

DEBUG = os.environ.get("DEBUG", False)
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
        "--username",
        help="Username to connect to API",
        required=True,
    )
    parser.add_argument(
        "--password",
        help="Username to connect to API",
        required=True,
    )
    parser.add_argument(
        "--base-url",
        help="URL to connect to - No trailing /",
        required=True,
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    logger.info(
        f"""Starting Kubernetes Event Listener"
                Namespace: {args.namespace}
                Label Selector: {args.label_selector}
                URL: {args.base_url}"""
    )

    init(
        namespace=args.namespace,
        label_selector=args.label_selector,
        username=args.username,
        password=args.password,
        base_url=args.base_url,
    )
