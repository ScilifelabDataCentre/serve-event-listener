import argparse
import logging

import requests
from init_stream import init
from kubernetes import watch

# Configure the logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description="Kubernetes Event Listener")
    parser.add_argument(
        "--namespace",
        required=True,
        help="Kubernetes namespace to watch",
        default="default",
    )
    parser.add_argument(
        "--label-selector",
        required=True,
        help="Label selector for filtering pods",
        default="type=app",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    # Configure logger to write to a file
    log_file = "event_listener.log"
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.INFO)
    file_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    logger.info(
        "Starting Kubernetes Event Listener with namespace: %s, label selector: %s",
        args.namespace,
        args.label_selector,
    )

    init(args.namespace, args.label_selector)
