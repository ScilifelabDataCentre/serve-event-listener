import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Tuple, Union

import requests
from kubernetes import client
from kubernetes.client.exceptions import ApiException
from kubernetes.client.models import V1PodStatus

logger = logging.getLogger(__name__)

USERNAME = os.environ.get("USERNAME", None)
PASSWORD = os.environ.get("PASSWORD", None)
KUBECONFIG = os.environ.get("KUBECONFIG", None)

# Note: The k8s status map is not used unless translation mapping is enabled
K8S_STATUS_MAP = {
    "CrashLoopBackOff": "Error",
    "Completed": "Retrying...",
    "ContainerCreating": "Created",
    "PodInitializing": "Pending",
    "ErrImagePull": "Image Error",
    "ImagePullBackOff": "Image Error",
    "PostStartHookError": "Pod Error",
}


class StatusData:
    def __init__(self):
        self.status_data = {}
        self.k8s_api_client = None
        self.namespace = "default"

    @staticmethod
    def determine_status_from_k8s(
        status_object: V1PodStatus, translate_status: bool = False
    ) -> Tuple[str, str, str]:
        """
        Get the status of a Kubernetes pod.
        First checks init_container_statuses, then container_statuses
        Properties used to translate the pod status:
        - container.state
        - state.terminated.reason
        - state.waiting, waiting.reason
        - state.running and container_status.ready

        Parameters:
        - status_object (dict): The Kubernetes status object.
        - translate_status (bool): A boolean value indicating whether to translate the status values to the map.

        Returns:
        - Tuple[str, str, str]: The status of the pod, container message, pod message
        """
        empty_message = ""
        pod_message = status_object.message if status_object.message else empty_message

        def process_container_statuses(container_statuses, init_containers=False):
            for container_status in container_statuses:
                state = container_status.state

                terminated = state.terminated
                if terminated:
                    if init_containers and terminated.reason == "Completed":
                        break
                    else:
                        return (
                            (
                                StatusData.get_mapped_status(terminated.reason)
                                if translate_status
                                else terminated.reason
                            ),
                            terminated.message if terminated.message else empty_message,
                            pod_message,
                        )

                waiting = state.waiting

                if waiting:
                    return (
                        (
                            StatusData.get_mapped_status(waiting.reason)
                            if translate_status
                            else waiting.reason
                        ),
                        waiting.message if waiting.message else empty_message,
                        pod_message,
                    )
                else:
                    running = state.running
                    ready = container_status.ready
                    if running and ready:
                        return "Running", empty_message, pod_message
                    else:
                        return "Pending", empty_message, pod_message
            else:
                return None

        init_container_statuses = status_object.init_container_statuses
        container_statuses = status_object.container_statuses

        if init_container_statuses is not None:
            result = process_container_statuses(
                init_container_statuses, init_containers=True
            )
            if result:
                return result

        if container_statuses is not None:
            result = process_container_statuses(container_statuses)
            if result:
                return result

        return status_object.phase, empty_message, pod_message

    @staticmethod
    def get_mapped_status(reason: str) -> str:
        return K8S_STATUS_MAP.get(reason, reason)

    @staticmethod
    def get_timestamp_as_str() -> str:
        """
        Get the current UTC time as a formatted string.

        Returns:
            str: The current UTC time in ISO format with milliseconds.
        """
        current_utc_time = (
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        )
        return current_utc_time

    def set_k8s_api_client(self, k8s_api_client: client.CoreV1Api, namespace: str):
        self.k8s_api_client = k8s_api_client
        self.namespace = namespace

    def fetch_pod_phases_in_release_from_k8s_api(
        self, release: str, response_limit: int = 1000
    ) -> list[str]:
        """
        Get the actual pod phases of the pods in the release from k8s via the client API.
        Because this can be as costly operation it is only used at critical times such as deleted pods.

        Parameters:
        - release (str): The release

        Returns:
        - list[str]: A list of pod phases
        - response_limit (int): The maximum number of objects to return from the k8s API call.

        If no pod matches the release, then returns empty list.
        """
        logger.debug(
            f"Getting the nr of pods in release {release} directly from k8s via the api client"
        )

        apps_v1 = client.AppsV1Api()

        deployments = apps_v1.list_namespaced_deployment(
            namespace=self.namespace, label_selector=f"release={release}"
        )

        if not deployments.items:
            return []

        # Assume the first deployment is the one we are interested in
        deployment = deployments.items[0]
        deployment_name = deployment.metadata.name

        pods = self.k8s_api_client.list_namespaced_pod(
            namespace=self.namespace,
            label_selector=f"app={deployment_name}",
            limit=response_limit,
            timeout_seconds=120,
            watch=False,
        )

        phases = []
        for pod in pods.items:
            phases.append(pod.status.phase)

        return phases

    def fetch_status_from_k8s_api(
        self, release: str, response_limit: int = 1000
    ) -> Tuple[str, str, str]:
        """
        Get the actual status of a release from k8s via the client API.
        Because this can be as costly operation it is only used at critical times such as deleted pods.

        Parameters:
        - release (str): The release
        - response_limit (int): The maximum number of objects to return from the k8s API call.

        Returns:
        - Tuple[str, str, str]: The status of the pod, container message, pod message

        If no pod matches the release, then return None, "", ""
        """
        logger.debug(
            f"Getting the status of release {release} directly from k8s via the api client"
        )

        status = None
        container_message = pod_message = ""

        try:
            api_response = self.k8s_api_client.list_namespaced_pod(
                self.namespace, limit=response_limit, timeout_seconds=120, watch=False
            )

            for pod in api_response.items:
                if pod.metadata.labels.get("release") == release:
                    pod_status, container_message, pod_message = (
                        StatusData.determine_status_from_k8s(pod.status)
                    )
                    if status is None:
                        status = pod_status
                        logger.debug(
                            f"Preliminary status of release {release} set from None to {status}"
                        )
                    elif status == "Deleted":
                        # All other statuses override Deleted
                        status = pod_status
                        logger.debug(
                            f"Preliminary status of release {release} set from Deleted to {status}"
                        )
                    elif pod_status == "Running":
                        # Running overrides all other statuses
                        status = pod_status
                        logger.debug(
                            f"Preliminary status of release {release} set to {status}"
                        )
        except ApiException as e:
            logger.warning(
                f"Exception when calling CoreV1Api->list_namespaced_pod. {e}"
            )

        return status, container_message, pod_message

    def update(self, event: dict) -> None:
        """
        Process a Kubernetes pod event and update the status_data.

        Parameters:
        - event (dict): The Kubernetes pod event.
        - status_data (dict): Dictionary containing status info.

        Sets:
        - status_data (dict): Updated dictionary containing status info.
        - release (str): The release of the updated status
        """
        pod = event.get("object", None)

        # TODO: Try catch here instead
        if pod:
            release = pod.metadata.labels.get("release")

            logger.info(
                f"--- Event triggered update status data from release {release}"
            )

            status_object = pod.status

            status, container_message, pod_message = (
                StatusData.determine_status_from_k8s(status_object)
            )

            logger.debug(
                f"Pod status converted to AppStatus={status}, \
                         ContMessage:{container_message}, \
                         PodMessage:{pod_message}"
            )

            creation_timestamp = pod.metadata.creation_timestamp
            deletion_timestamp = pod.metadata.deletion_timestamp

            self.status_data = self.update_or_create_status(
                self.status_data,
                status,
                release,
                creation_timestamp,
                deletion_timestamp,
            )

            self.status_data[release]["pod-msg"] = pod_message
            self.status_data[release]["container-msg"] = container_message

    def get_post_data(self) -> dict:
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
        release = self.get_latest_release()
        data = self.status_data[release]

        post_data = {
            "release": release,
            "new-status": data.get("status", None),
            "event-ts": data.get("event-ts", None),
            "event-msg": {
                "pod-msg": data.get("pod-msg", None),
                "container-msg": data.get("container-msg", None),
            },
        }
        logger.debug("Converting to POST data")
        return post_data

    def update_or_create_status(
        self,
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

        log_msg = ""
        if release in status_data:
            log_msg = f"Status data before update:{status_data[release]}"
        else:
            log_msg = "Release not in status_data. Adding now."

        logger.debug(
            f"Release {release}. {log_msg} \
                    creation_timestamp={creation_timestamp}, deletion_timestamp={deletion_timestamp}"
        )

        if (
            release not in status_data
            or creation_timestamp >= status_data[release]["creation_timestamp"]
            or deletion_timestamp is not None
        ):

            if deletion_timestamp is not None:
                # Status Deleted may be be a disruptive event to send to the client app
                # Therefore we double check if there are other pods in the release
                if (
                    deletion_timestamp > status_data[release]["creation_timestamp"]
                    or creation_timestamp is not None
                    and deletion_timestamp > creation_timestamp
                ):
                    if self.k8s_api_client is None:
                        logger.warning("No k8s API client: k8s_api_client is None")

                    if self.k8s_api_client:
                        # Only use if the k8s client api has been set
                        # Unit tests for example do not currently set a k8s api
                        pod_phases = self.fetch_pod_phases_in_release_from_k8s_api(
                            release
                        )
                        logger.debug(
                            f"Fetched {len(pod_phases)} for release {release} from k8s"
                        )

                        if len(pod_phases) <= 1:
                            # There are no other pods in this release. Set status to Deleted
                            status = "Deleted"

                if status != "Deleted":
                    deletion_timestamp = None

            status_data[release] = {
                "creation_timestamp": creation_timestamp,
                "deletion_timestamp": deletion_timestamp,
                "status": status,
                "event-ts": StatusData.get_timestamp_as_str(),
                "sent": False,
            }
            logger.debug(
                f"UPDATING STATUS DATA FOR {release} WITH STATUS {status_data[release]['status']}"
            )
        else:
            logger.debug("No update was made")
        return status_data

    def get_latest_release(self):
        # TODO: add exception if event-ts is none.
        latest_release = max(
            self.status_data, key=lambda k: self.status_data[k]["event-ts"]
        )
        return latest_release
