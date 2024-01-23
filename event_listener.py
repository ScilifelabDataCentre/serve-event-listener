import requests
from kubernetes import watch

from utils import (get_status, setup_client, sync_all_statuses,
                   update_status_data)


def init_event_listener(namespace, label_selector):
    """
    The event listener takes the latest event and checks if a corresponding appinstance
    should be updated. It uses the creation timestamp to always use the status of the youngest pod
    in a helm release.
    """
    k8s_api = setup_client()
    k8s_watch = watch.Watch()

    try:
        start_stream(k8s_watch, k8s_api, namespace, label_selector)

    except Exception as exc:
        print("Event listener exception occured", exc)


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

    for event in k8s_watch.stream(
        k8s_api.list_namespaced_pod, namespace=namespace, label_selector=label_selector
    ):
        status_data = update_status_data(event, status_data)
        # TODO: send request

    return False
