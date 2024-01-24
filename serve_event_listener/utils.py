import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Tuple, Union

import requests
from kubernetes import client, config
from kubernetes.client.models import V1PodStatus

logger = logging.getLogger(__name__)

K8S_STATUS_MAP = {
    "CrashLoopBackOff": "Error",
    "Completed": "Retrying...",
    "ContainerCreating": "Created",
    "PodInitializing": "Pending",
    "ErrImagePull": "Image Error",
    "ImagePullBackOff": "Image Error",
}

KUBECONFIG = os.environ.get("KUBECONFIG", None)


def setup_client():
    """
    Sets up the kubernetes python client
    """
    logger.info("Setting up kubernetes client")
    try:
        if KUBECONFIG:
            logger.debug("Attempting to load KUBECONFIG")
            config.load_kube_config(KUBECONFIG)
        else:
            logger.warning("No KUBECONFIG provided - attemting to use default config")
            config.incluster_config.load_incluster_config()

    except config.ConfigException as e:
        logging.error("An exception occurred while setting the cluster config.")
        logging.exception(e)  # Log the full exception traceback

        raise config.ConfigException(
            "Could not set the cluster config properly."
        ) from e

    k8s_api = client.CoreV1Api()

    return k8s_api


def update_status_data(event: dict, status_data: dict) -> dict:
    """
    Process a Kubernetes pod event and update the status_data.

    Parameters:
    - event (dict): The Kubernetes pod event.
    - status_data (dict): Dictionary containing status info.

    Returns:
    - status_data (dict): Updated dictionary containing status info.
    - release (str): The release of the updated status
    """
    logger.debug("Event triggered update_status_data")

    pod = event.get("object", None)

    if not pod:
        return {}

    status_object = pod.status
    if not status_object:
        return {}

    status, container_message = get_status(status_object)
    release = pod.metadata.labels.get("release")

    logger.debug(f"Event triggered from release {release}")
    logger.debug(f"Status: {status} - Message: {container_message}")

    creation_timestamp = pod.metadata.creation_timestamp
    deletion_timestamp = pod.metadata.deletion_timestamp

    status_data = update_or_create_status(
        status_data, status, release, creation_timestamp, deletion_timestamp
    )

    status_data[release]["pod-msg"] = status_object.message
    status_data[release]["container-msg"] = container_message

    return status_data


def update_or_create_status(
    status_data: Dict[str, Any],
    status: str,
    release: str,
    creation_timestamp: datetime,
    deletion_timestamp: Union[datetime, None],
) -> Dict[str, Any]:
    """
    Update the status data for a release.

    Args:
        status_data (Dict): The existing status data.
        status (str): The new status.
        release (str): The release identifier.
        creation_timestamp (datetime): The creation timestamp.
        deletion_timestamp (Union[datetime, None]): The deletion timestamp or None.

    Returns:
        Dict: Updated status data.
    """
    if (
        release not in status_data
        or creation_timestamp >= status_data[release]["creation_timestamp"]
    ):
        status_data[release] = {
            "creation_timestamp": creation_timestamp,
            "deletion_timestamp": deletion_timestamp,
            "status": "Deleted" if deletion_timestamp else status,
            "event-ts": get_timestamp_as_str(),
            "sent": False,
        }
        logger.debug(
            f"UPDATING STATUS DATA FOR {release} WITH STATUS {status_data[release]['status']}"
        )
    else:
        logger.debug("No update was made")
    return status_data


def get_status(status_object: V1PodStatus) -> Tuple[str, str]:
    """
    Get the status of a Kubernetes pod.

    Parameters:
    - status_object (dict): The Kubernetes status object.

    Returns:
    - str: The status of the pod.
    """
    container_statuses = status_object.container_statuses
    empty_message = "Empty Message"

    if container_statuses is not None:
        for container_status in container_statuses:
            state = container_status.state

            terminated = state.terminated
            if terminated:
                return mapped_status(terminated.reason), terminated.message

            waiting = state.waiting

            if waiting:
                return mapped_status(waiting.reason), waiting.message

        else:
            running = state.running
            ready = container_status.ready
            if running and ready:
                return "Running", empty_message
            else:
                return "Pending", empty_message

    return status_object.phase, status_object.message


def mapped_status(reason: str) -> str:
    return K8S_STATUS_MAP.get(reason, reason)


def get_url() -> str:
    return ""


def convert_to_post_data(status_data: dict) -> dict:
    """
    The Serve API app-statuses expects a json on this form:
    {
        “token“: <token>,
        “new-status“: <new status>,
        “event-msg“: {“pod-msg“: <msg>, “container-msg“: <msg>},
        “event-ts“: <event timestamp>
    }

    Parameters:
    - status_data (dict): status_data dict from stream.

    Returns:
    - str: post data on the form explained above
    """
    token = "placeholder"

    post_data = {
        "token": token,
        "new-status": status_data["status"],
        "event-msg": {
            "pod-msg": status_data["pod-msg"],
            "container-msg": status_data["container-msg"],
            "event-ts": status_data["event-ts"],
        },
    }
    logger.debug("Converting to POST data")
    return post_data


def post(url: str, data: dict) -> int:
    try:
        token = "placeholder"
        headers = {"Authorization": f"Token {token}"}

        response = requests.post(url, data=data, headers=headers, verify=False)

        logger.debug(f"RESPONSE STATUS CODE: {response.status_code}")
        logger.debug(f"RESPONSE TEXT: {response.text}")

    except requests.exceptions.RequestException:
        logger.error("Service did not respond.")

    return response.status_code


def get_timestamp_as_str() -> str:
    current_utc_time = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    return str(current_utc_time)
