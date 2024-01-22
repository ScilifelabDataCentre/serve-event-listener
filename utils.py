from kubernetes import client, config, watch

K8S_STATUS_MAP = {
    "CrashLoopBackOff": "Error",
    "Completed": "Retrying...",
    "ContainerCreating": "Created",
    "PodInitializing": "Pending",
}

def setup_client():
    """
    Sets up the kubernetes python client and event streamer
    """
    try:
        config.load_incluster_config()
    except config.ConfigException:
        try:
            config.load_kube_config(settings.KUBECONFIG)
        except config.ConfigException as e:
            raise config.ConfigException(
                "Could not set the cluster config. Try to use the cluster.conf file or set incluster config"
            ) from e

    k8s_api = client.CoreV1Api()

    return k8s_api



def update_status_data(event: dict, status_data: dict) -> dict:
    """
    Process a Kubernetes pod event and update the status_data.

    Parameters:
    - event (dict): The Kubernetes pod event.
    - status_data (dict): Dictionary containing status information for releases.

    Returns:
    - status_data (dict): Updated dictionary containing status information for releases.
    """
    pod = event["object"]
    status = get_status(pod)[:15]
    release = pod.metadata.labels.get("release")
    creation_timestamp = pod.metadata.creation_timestamp
    deletion_timestamp = pod.metadata.deletion_timestamp

    # Case 1 - Set unseen release
    if release not in status_data or creation_timestamp > status_data[release]["creation_timestamp"]:
        status_data[release] = {
            "creation_timestamp": creation_timestamp,
            "deletion_timestamp": deletion_timestamp,
            "status": "Deleted" if deletion_timestamp else status,
        }
    return status_data

def get_status(pod: dict) -> str:
    """
    Get the status of a Kubernetes pod.

    Parameters:
    - pod (dict): The Kubernetes pod object.

    Returns:
    - str: The status of the pod.
    """
    container_statuses = pod.status.container_statuses

    if container_statuses is not None:
        for container_status in container_statuses:
            state = container_status.state

            if state is not None:
                terminated = state.terminated

                if terminated is not None:
                    reason = terminated.reason
                    return mapped_status(reason)

                waiting = state.waiting

                if waiting is not None:
                    reason = waiting.reason
                    return mapped_status(reason)
        else:
            running = state.running
            ready = container_status.ready
            if running and ready:
                return "Running"
            else:
                return "Pending"

    return pod.status.phase


def mapped_status(reason: str) -> str:
    return K8S_STATUS_MAP.get(reason, reason)


def sync_all_statuses(namespace, label_selector):
    """
    Syncs the status of all apps with a pod that is on the cluster
    """
    k8s_api = setup_client()
    for pod in k8s_api.list_namespaced_pod(namespace=namespace, label_selector=label_selector).items:
        status = pod.status.phase
        release = pod.metadata.labels["release"]
        #TODO: send request to update        
