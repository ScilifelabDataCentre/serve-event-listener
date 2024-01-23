import logging
import time

import requests
import urllib3
from kubernetes import watch
from utils import setup_client, update_status_data

logger = logging.getLogger(__name__)


def init(namespace, label_selector, max_retries=10, retry_delay=2):
    """
    The event listener takes the latest event and checks if a corresponding appinstance
    should be updated. It uses the creation timestamp to always use the status of the youngest pod
    in a helm release.
    """
    retries = 0

    while retries < max_retries:
        try:
            k8s_api = setup_client()
            k8s_watch = watch.Watch()

            start_stream(k8s_watch, k8s_api, namespace, label_selector)

        except urllib3.exceptions.ProtocolError as e:
            logger.error(f"ProtocolError occurred: {e!r}")
            logger.info(f"Retrying in {retry_delay} seconds...")
            time.sleep(retry_delay)
            retries += 1

        except Exception as e:
            logger.error("Event listener exception occurred: %s")
            logger.exception(e)
            break  # Break the loop for other exceptions

    if retries == max_retries:
        logger.error("Max retries reached. Unable to establish the connection.")


def start_stream(k8s_watch, k8s_api, namespace, label_selector):
    """
    Process the Kubernetes pod stream and update the status_data.

    Parameters:
    - k8s_watch: Kubernetes watch instance.
    - k8s_api: Kubernetes API instance.
    - namespace (str): The Kubernetes namespace to watch.
    - label_selector (str): The label selector for filtering pods.

    Returns:
    - dict: Updated status_data.
    """
    status_data = {}
    logger.info("Initializing event stream")
    for event in k8s_watch.stream(
        k8s_api.list_namespaced_pod, namespace=namespace, label_selector=label_selector
    ):
        status_data = update_status_data(event, status_data)
        # TODO: send request

    return False
