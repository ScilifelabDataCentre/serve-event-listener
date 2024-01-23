import os
from typing import Any, Dict

from kubernetes import client, config

K8S_STATUS_MAP = {
    "CrashLoopBackOff": "Error",
    "Completed": "Retrying...",
    "ContainerCreating": "Created",
    "PodInitializing": "Pending",
}

KUBECONFIG = os.environ.get("KUBECONFIG", None)


def setup_client():
    """
    Sets up the kubernetes python client
    """

    try:
        config.load_kube_config(KUBECONFIG)
    except config.ConfigException as e:
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
    """
    pod = event["object"]
    status_object = pod.status
    status = get_status(status_object)[:15]
    release = pod.metadata.labels.get("release")
    creation_timestamp = pod.metadata.creation_timestamp
    deletion_timestamp = pod.metadata.deletion_timestamp

    # Case 1 - Set unseen release
    if (
        release not in status_data
        or creation_timestamp > status_data[release]["creation_timestamp"]
    ):
        status_data[release] = {
            "creation_timestamp": creation_timestamp,
            "deletion_timestamp": deletion_timestamp,
            "status": "Deleted" if deletion_timestamp else status,
        }
    return status_data


def get_status(status_object: Dict[str, Any]) -> str:
    """
    Get the status of a Kubernetes pod.

    Parameters:
    - status_object (dict): The Kubernetes status object.

    Returns:
    - str: The status of the pod.
    """
    container_statuses = status_object.get("container_statuses", None)

    if container_statuses is not None:
        for container_status in container_statuses:
            state = container_status.get("state", {})

            terminated = state.get("terminated", None)
            if terminated:
                return mapped_status(terminated.get("reason", "Terminated"))

            waiting = state.get("waiting", None)
            if waiting:
                return mapped_status(waiting.get("reason", "Waiting"))

        else:
            running = state.get("running", None)
            ready = container_status.get("ready", None)
            if running and ready:
                return "Running"
            else:
                return "Pending"

    return status_object.get("phase", "Error")


def mapped_status(reason: str) -> str:
    return K8S_STATUS_MAP.get(reason, reason)


def sync_all_statuses(namespace, label_selector):
    """
    Syncs the status of all apps with a pod that is on the cluster
    """
    k8s_api = setup_client()
    for pod in k8s_api.list_namespaced_pod(
        namespace=namespace, label_selector=label_selector
    ).items:
        status = pod.status.phase
        release = pod.metadata.labels["release"]
        # TODO: send request to update
