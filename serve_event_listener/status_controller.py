import logging
import os
import threading
import time
from typing import Any, Optional, Union

import requests
import urllib3
from kubernetes import client, config, watch
from status_data import StatusData
from status_queue import StatusQueue

logger = logging.getLogger(__name__)

USERNAME = os.environ.get("USERNAME", None)
PASSWORD = os.environ.get("PASSWORD", None)
KUBECONFIG = os.environ.get("KUBECONFIG", None)

BASE_URL = os.environ.get("BASE_URL", "http://studio.192.168.0.130.nip.io:8080")
TOKEN_API_ENDPOINT = os.environ.get(
    "TOKEN_API_ENDPOINT", BASE_URL + "/api/v1/token-auth/"
)
APP_STATUS_API_ENDPOINT = os.environ.get(
    "APP_STATUS_API_ENDPOINT", BASE_URL + "/api/v1/app-status/"
)


class EventListener:
    """
    EventListener class for handling Kubernetes events.
    """

    def __init__(
        self, namespace: str = "default", label_selector: str = "type=app"
    ) -> None:
        """
        Initializes the EventListener object.

        Parameters:
        - namespace (str): The Kubernetes namespace.
        - label_selector (str): The label selector for filtering events.
        """
        logger.info("Creating EventListener object")
        self.namespace = namespace
        self.label_selector = label_selector
        self.setup_complete = False

    @property
    def status_data_dict(self) -> dict:
        """
        Property to get the status data dictionary.

        Returns:
        - dict: The status data dictionary.
        """
        return self._status_data.status_data

    @property
    def status_data(self) -> Any:
        """
        Property to get the status data object.

        Returns:
        - Any: The status data object.
        """
        return self._status_data

    def setup(self, **kwargs: Optional[Any]) -> None:
        """
        Sets up the EventListener object.

        Parameters:
        - **kwargs: Additional setup parameters.
        """
        logger.info(
            "\n\n\t{}\n\t   Running Setup Process \n\t{}\n".format("#" * 30, "#" * 30)
        )
        try:
            self.check_status()
            self.setup_client()
            self.fetch_token()
            self._status_data = StatusData()
            self._status_queue = StatusQueue(self.post, self.token)
            self.setup_complete = True
        except Exception:
            # TODO: Add specific exceptions here
            logger.error("Setup failed")

    def listen(self) -> None:
        """
        Initializes the event stream and starts listening for events.

        Parameters:
        - **kwargs: Additional parameters for listening.
        """
        logger.info(
            "\n\n\t{}\n\t  Initializing event stream\n\t{}\n".format("#" * 30, "#" * 30)
        )

        max_retries = 10
        retry_delay = 2

        if self.setup_complete:
            # Start queue in a separate thread
            status_queue_thread = threading.Thread(target=self._status_queue.process)
            status_queue_thread.start()

            retries = 0
            while retries < max_retries:
                try:
                    for event in self.watch.stream(
                        self.client.list_namespaced_pod,
                        namespace=self.namespace,
                        label_selector=self.label_selector,
                    ):
                        # Update status_data_object with new event
                        self.status_data.update(event)

                        # Extract the data that should be sent to API
                        data = self.status_data.get_post_data()
                        print(data)
                        # Add to queue. Queue handles post and return codes
                        self._status_queue.add(data)

                except urllib3.exceptions.ProtocolError as e:
                    logger.error(f"ProtocolError occurred: {e!r}")
                    logger.info(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    retries += 1

                except Exception as e:
                    logger.error("Event listener exception occurred:")
                    logger.exception(e)
                    self._status_queue.stop_processing()
                    break  # Break the loop for other exceptions

            if retries == max_retries:
                self._status_queue.stop_processing()
                logger.error("Max retries reached. Unable to establish the connection.")
        else:
            logger.warning("Setup not completed - run .setup() first")

    def check_status(self) -> bool:
        """
        Checks the status of the EventListener.

        Returns:
        - bool: True if the status is okay, False otherwise.
        """
        response = self.get(url=BASE_URL + "/openapi/v1/are-you-there")
        if response.status_code == 200:
            return True
        else:
            return False

    def setup_client(self) -> None:
        """
        Sets up the Kubernetes Python client.
        """
        logger.info("Setting up Kubernetes client")
        try:
            if KUBECONFIG:
                logger.debug("Attempting to load KUBECONFIG")
                config.load_kube_config(KUBECONFIG)
            else:
                logger.warning(
                    "No KUBECONFIG provided - attempting to use default config"
                )
                config.incluster_config.load_incluster_config()

        except config.ConfigException as e:
            logging.error("An exception occurred while setting the cluster config.")
            logging.exception(e)  # Log the full exception traceback

            raise config.ConfigException(
                "Could not set the cluster config properly."
            ) from e

        logger.info("Kubernetes client successfully set")
        self.client = client.CoreV1Api()
        self.watch = watch.Watch()

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
        logger.info("Token fetched successfully")
        self.token = token

    def post(self, url: str, data: dict, headers: Union[None, dict] = None):
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
            response = requests.post(url=url, json=data, headers=headers, verify=False)
            logger.info(f"POST returned - Status code: {response.status_code}")

        except requests.exceptions.RequestException:
            logger.error("Service did not respond.")
            response = None

        return response

    def get(self, url: str, headers: Union[None, dict] = None):
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

        except requests.exceptions.RequestException:
            logger.error("Service did not respond.")
            response = None

        return response
