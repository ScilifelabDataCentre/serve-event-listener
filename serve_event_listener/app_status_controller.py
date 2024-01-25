import queue
import requests
import threading
import time
import os
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Tuple, Union

import requests
from kubernetes import client, config
from kubernetes.client.models import V1PodStatus

logger = logging.getLogger(__name__)

BASE_URL = os.environ.get("BASE_URL", None)
USERNAME = os.environ.get("USERNAME", None)
PASSWORD = os.environ.get("PASSWORD", None)
KUBECONFIG = os.environ.get("KUBECONFIG", None)

BASE_URL = "http://studio.127.0.0.1.nip.io:8080"
USERNAME = "x@x.se"
PASSWORD = "x"

TOKEN_API_ENDPOINT = BASE_URL + "/api/token-auth/"
APP_STATUS_API_ENDPOINT = BASE_URL + "/apps/"

K8S_STATUS_MAP = {
    "CrashLoopBackOff": "Error",
    "Completed": "Retrying...",
    "ContainerCreating": "Created",
    "PodInitializing": "Pending",
    "ErrImagePull": "Image Error",
    "ImagePullBackOff": "Image Error",
}


class EventListener:
    
    def __init__(self):
        logger.info("Creating EventListener object")
        self._status_data = StatusData()
        #headers = {"Authorization": f"Token {self.token}"}
    
    @property
    def status_data(self):
        return self._status_data.status_data
    
    @property
    def status_data_object(self):
        return self._status_data
    
    def listen():
        pass 
    
    def setup(self, **kwargs):
        if self.check_status():
            self.setup_client()
            self.fetch_token()
        pass
    
    def check_status(self):
        response = self.get(url=BASE_URL+"/openapi/v1/are-you-there")
        if response.status_code == 200:
            return True
        else:
            return False
    
    def setup_client(self):
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
        logger.info("Kubernetes client successfully set")
        self.client = client.CoreV1Api()

    
    def fetch_token(self):
        """
        Retrieve an authentication token by sending a POST request with the provided data.

        Args:
            url (str): The URL to send the POST request to.
            data (dict): The data to be included in the POST request.

        Returns:
            str: The authentication token obtained from the response.
        Raises:
            KeyError: If the response does not contain a valid token.
            requests.exceptions.RequestException: If the service does not respond.
        """
        data = {"username": USERNAME, "password": PASSWORD}
        try:
            response = self.post(url=TOKEN_API_ENDPOINT, data=data)
            response_json = response.json()
            token = response_json["token"]
            logger.debug(f"FETCHING TOKEN: {token}")

        except KeyError as e:
            message = "No token was fetched - Are the credentials correct?"
            logger.error(message)
            raise KeyError(message) from e

        self.token = token
        
    def post(self, url:str, data: dict, headers: Union[None, dict] = None) -> int:
        """
        Send a POST request to the specified URL with the provided data and token.

        Args:
            url (str): The URL to send the POST request to.
            data (dict): The data to be included in the POST request.
            header (None or dict): header for the request.

        Returns:
            int: The HTTP status code of the response.
        """
        try:
            response = requests.post(url=url, data=data, headers=headers, verify=False)
            logger.info(f"POST returned - Status code: {response.status_code}")

        except requests.exceptions.RequestException as e:
            logger.error("Service did not respond.")
            response = None

        return response
        
    def get(self, url:str, headers: Union[None, dict] = None) -> int:
        """
        Send a POST request to the specified URL with the provided data and token.

        Args:
            url (str): The URL to send the POST request to.
            data (dict): The data to be included in the POST request.
            header (None or dict): header for the request.

        Returns:
            int: The HTTP status code of the response.
        """
        try:
            response = requests.get(url=url, headers=headers, verify=False)
            logger.info(f"GET returned status code: {response.status_code}")

        except requests.exceptions.RequestException as e:
            logger.error("Service did not respond.")
            response = None

        return response

class StatusData:
    
    def __init__(self):
        self.status_data = {}
    
    def update(self, event: dict) -> None:
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

        #TODO: Try catch here instead
        if pod:
            status_object = pod.status

            status, pod_message, container_message = self.get_status(status_object)
            release = pod.metadata.labels.get("release")

            logger.debug(f"Event triggered from release {release}")
            logger.debug(f"Status: {status} - Message: {container_message}")

            creation_timestamp = pod.metadata.creation_timestamp
            deletion_timestamp = pod.metadata.deletion_timestamp

            self.status_data = self.update_or_create_status(
                self.status_data, status, release, creation_timestamp, deletion_timestamp
            )

            self.status_data[release]["pod-msg"] = pod_message
            self.status_data[release]["container-msg"] = container_message
    
    
    def get_status(self, status_object: V1PodStatus) -> Tuple[str, str, str]:
        """
        Get the status of a Kubernetes pod.

        Parameters:
        - status_object (dict): The Kubernetes status object.

        Returns:
        - str: The status of the pod.
        """
        empty_message = "empty message"
        pod_message = status_object.message if status_object.message else empty_message

        container_statuses = status_object.container_statuses

        if container_statuses is not None:
            for container_status in container_statuses:
                state = container_status.state

                terminated = state.terminated
                if terminated:
                    return self.mapped_status(terminated.reason), terminated.message, pod_message

                waiting = state.waiting

                if waiting:
                    return self.mapped_status(waiting.reason), waiting.message, pod_message

            else:
                running = state.running
                ready = container_status.ready
                if running and ready:
                    return "Running", empty_message, pod_message
                else:
                    return "Pending", empty_message, pod_message

        return status_object.phase, empty_message, pod_message

    
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
        if (
            release not in status_data
            or creation_timestamp >= status_data[release]["creation_timestamp"]
        ):
            status_data[release] = {
                "creation_timestamp": creation_timestamp,
                "deletion_timestamp": deletion_timestamp,
                "status": "Deleted" if deletion_timestamp else status,
                "event-ts": self.get_timestamp_as_str(),
                "sent": False,
            }
            logger.debug(
                f"UPDATING STATUS DATA FOR {release} WITH STATUS {status_data[release]['status']}"
            )
        else:
            logger.debug("No update was made")
        return status_data
    
    def mapped_status(reason: str) -> str:
        return K8S_STATUS_MAP.get(reason, reason)
    
    def get_timestamp_as_str() -> str:
        """
        Get the current UTC time as a formatted string.

        Returns:
            str: The current UTC time in ISO format with milliseconds.
        """
        current_utc_time = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        return str(current_utc_time)
    

event_listener = EventListener()
