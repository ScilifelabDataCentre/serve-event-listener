import logging
import os
import threading
import time
from typing import Any, Mapping, Optional

import requests
import urllib3
from kubernetes import client, config, watch
from kubernetes.client.exceptions import ApiException
from urllib3.exceptions import HTTPError

from serve_event_listener.el_types import StatusRecord
from serve_event_listener.http_client import get as http_get
from serve_event_listener.http_client import make_session
from serve_event_listener.http_client import post as http_post
from serve_event_listener.http_client import tls_verify_from_env
from serve_event_listener.probing import AppAvailabilityProbe
from serve_event_listener.status_data import StatusData
from serve_event_listener.status_queue import StatusQueue

logger = logging.getLogger(__name__)

# Disable urllib3 and Kubernetes client debug logs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("kubernetes").setLevel(logging.WARNING)

USERNAME = os.environ.get("USERNAME", None)
PASSWORD = os.environ.get("PASSWORD", None)
KUBECONFIG = os.environ.get("KUBECONFIG", None)

BASE_URL = os.environ.get("BASE_URL", "http://studio.127.0.0.1.nip.io:8080")
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

        self.setup_complete: bool = False
        self._status_data: StatusData = StatusData()
        self._status_queue: Optional[StatusQueue] = None
        self._prober: Optional[AppAvailabilityProbe] = None
        self.token: Optional[str] = None

        # Track the latest resourceVersion
        self.resource_version: Optional[str] = None

        # Prepare session and settings for http get and post requests
        self.verify_tls = tls_verify_from_env()  # True/False or cert path
        self.session: requests.Session = make_session(
            total_retries=3, verify=self.verify_tls
        )
        self.token_fetcher = self.fetch_token

        # Same settings as in http client but repeated for clarity
        self.timeout: tuple[float, float] = (3.05, 20.0)
        self.backoff_seconds: tuple[int, int, int] = (1, 2, 4)

    @property
    def status_data_dict(self) -> Mapping[str, StatusRecord]:
        """
        Property to get the status data dictionary.

        Returns:
        - dict: The status data dictionary.
        """
        return self._status_data.status_data

    @property
    def status_data(self) -> StatusData:
        """
        Property to get the status data object.

        Returns:
        - Any: The status data object.
        """
        return self._status_data

    @property
    def client_api_ping_endpoint(self) -> str:
        """A URL of the client target API for checking the status of the API is UP or DOWN"""
        return BASE_URL + "/openapi/v1/are-you-there"

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
            if not self.check_serve_api_status():
                logger.error(
                    "Unable to start the event listener service. The receiving API did not respond."
                )
                return

            self.setup_client()
            self.token = self.fetch_token()
            self._status_data.set_k8s_api_client(self.client, self.namespace)

            self._prober = AppAvailabilityProbe(
                self.session,
                timeout=self.timeout,
                backoff_seconds=self.backoff_seconds,
            )

            try:
                # Verify that the prober works for at least a known URL,
                test_url = self.client_api_ping_endpoint
                _ = self._prober.probe_url(test_url)
            except Exception as e:
                # Otherwise disable the probing feature
                logger.warning("Probing disabled: baseline probe failed (%s)", e)
                self._prober = None

            # StatusQueue now takes a shared session instead of post function
            self._status_queue = StatusQueue(
                self.session,
                APP_STATUS_API_ENDPOINT,
                self.token,
                self.fetch_token,
                prober=self._prober,
            )

            self.setup_complete = True
        except Exception as e:
            # TODO: Add specific exceptions here
            logger.error("Setup failed %s", e)

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

        # Duration in seconds to wait between retrying used when some exceptions occur
        retry_delay = 3

        if self.setup_complete:
            # Start queue in a separate thread
            status_queue_thread = threading.Thread(target=self._status_queue.process)
            status_queue_thread.start()

            # We refrain from more logging details from the urllib3 library for now
            # urllib3.add_stderr_logger()

            retries = 0
            while retries < max_retries:
                try:
                    # Start fresh if no resourceVersion (initial run or after 410)
                    if not self.resource_version:
                        logger.debug("No k8s resource version yet set. Getting now.")
                        self.resource_version = (
                            self.get_resource_version_from_pod_list()
                        )
                        logger.debug(
                            "Done getting resource version: %s", self.resource_version
                        )

                    # Stream events with resource_version
                    # Use a timeout of 4 minutes to avoid staleness
                    for event in self.watch.stream(
                        self.client.list_namespaced_pod,
                        namespace=self.namespace,
                        label_selector=self.label_selector,
                        resource_version=self.resource_version,
                        # server-side timeout, will be honored because watch = True
                        timeout_seconds=240,
                    ):
                        # Update resource_version to latest
                        self.resource_version = event[
                            "object"
                        ].metadata.resource_version

                        # Update status_data_object with new event
                        self.status_data.update(event)

                        record: StatusRecord = self.status_data.get_status_record()

                        # Add to queue. Queue handles post and return codes
                        self._status_queue.add(record)

                except urllib3.exceptions.ProtocolError as e:
                    logger.error(f"ProtocolError occurred: {e!r}")
                    logger.info("Retrying in %s seconds...", retry_delay)
                    time.sleep(retry_delay)
                    retries += 1

                except ApiException as e:
                    logger.info(f"ApiException occurred: {e!r}")
                    logger.debug(
                        "ApiException details: %s, %s, %s", e.status, e.body, e.headers
                    )

                    if e.status == 410:
                        # 410 Gone
                        logger.info(
                            "Watch closed due to outdated resource version. Re-establishing watch."
                        )
                        # Force fresh start
                        self.resource_version = None
                    elif e.status in [401, 403]:
                        logger.error("Authentication/Authorization error: %s", e)
                        retries += 1
                    elif 500 <= e.status < 600:
                        logger.error("Server error: %s", e)
                        retries += 1
                    else:
                        logger.error("Unexpected API exception: %s", e)
                        retries += 1

                    logger.info("Retrying in %s seconds...", retry_delay)

                    time.sleep(retry_delay)

                    # We no longer treat all ApiExceptions as flow-stopping errors
                    # retries += 1

                except ValueError as e:
                    # Handle value errors related to data processing
                    logger.error("Value error: %s", e)
                    retries += 1

                except (HTTPError, ConnectionError) as e:
                    logger.error("Network-related error: %s", e)
                    # A longer delay
                    time.sleep(5)

                except Exception as e:
                    logger.error("Event listener exception occurred:")
                    logger.exception(e)
                    self._status_queue.stop_processing()
                    break  # Break the loop for other exceptions

            if retries >= max_retries:
                self._status_queue.stop_processing()
                logger.error("Max retries reached. Unable to establish the connection.")
        else:
            logger.warning("Setup not completed - run .setup() first")

    def check_serve_api_status(self) -> bool:
        """
        Checks the status of the Serve API.

        Returns:
        - bool: True if the status is okay, False otherwise.
        """
        url = self.client_api_ping_endpoint
        logger.debug("Verifying that the server API is up and available via %s", url)

        # Using the new http get function
        response = http_get(
            self.session,
            url,
            timeout=self.timeout,
            backoff_seconds=self.backoff_seconds,
        )

        return bool(response and response.status_code == 200)

    def setup_client(self) -> None:
        """
        Sets up the Kubernetes Python client.
        """
        logger.info("Setting up Kubernetes client")
        try:
            if KUBECONFIG and os.path.exists(KUBECONFIG):
                logger.debug("Loading kubeconfig from KUBECONFIG = %s", KUBECONFIG)
                config.load_kube_config(KUBECONFIG)
            else:
                logger.info("No KUBECONFIG provided - attempting to use default config")
                config.incluster_config.load_incluster_config()

        except config.ConfigException as e:
            logging.error("An exception occurred while setting the cluster config.")
            logging.exception(e)  # Log the full exception traceback

            raise config.ConfigException(
                "Could not set the cluster config properly."
            ) from e

        # Disable client-side debug logging
        cfg = client.Configuration.get_default_copy()
        cfg.debug = False
        api_client = client.ApiClient(cfg)

        logger.info("Kubernetes client successfully set")
        self.client = client.CoreV1Api(api_client)

        # self.list_all_pods()

        self.watch = watch.Watch()

    def get_resource_version_from_pod_list(self) -> str:
        """
        Returns the resource version.
        """
        pods = self.client.list_namespaced_pod(
            namespace=self.namespace,
            watch=False,
            # client-side timeout: (connect_timeout, read_timeout)
            _request_timeout=(10, 30),
            # server-side timeout (may not be honored)
            timeout_seconds=30,
        )
        resource_version = pods.metadata.resource_version
        return resource_version

    def list_all_pods(self) -> None:
        """
        Lists all pods and logs their status.
        """
        logger.info("Listing all pods and their status codes")

        try:
            api_response = self.client.list_namespaced_pod(
                namespace=self.namespace,
                limit=5000,
                watch=False,
                # client-side timeout: (connect_timeout, read_timeout)
                _request_timeout=(30, 60),
                # server-side timeout (may not be honored)
                timeout_seconds=120,
            )

            for pod in api_response.items:
                release = pod.metadata.labels.get("release")
                app_status = StatusData.determine_status_from_k8s(pod.status)
                logger.info(
                    "Release=%s, %s with status %s",
                    release,
                    pod.metadata.name,
                    app_status,
                )
        except ApiException as e:
            logger.warning(
                "Exception when calling CoreV1Api->list_namespaced_pod. %s", e
            )

    def fetch_token(self) -> str:
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

        # Use the shared session and the central call options.
        # NOTE: token_fetcher=None — we don't want the client to recurse to fetch a token
        # while we're already fetching one.
        response: Optional[requests.Response] = http_post(
            self.session,
            TOKEN_API_ENDPOINT,
            data=data,
            timeout=self.timeout,
            backoff_seconds=self.backoff_seconds,
            token_fetcher=None,
        )

        if response is None:
            # Network-layer exception already logged by the wrapper; surface a clear error.
            raise requests.exceptions.RequestException("Token endpoint unreachable")

        if response.status_code != 200:
            # Include status + short body for diagnostics.
            raise requests.exceptions.RequestException(
                f"Token endpoint returned {response.status_code}: {response.text[:200]}"
            )

        try:
            payload = response.json()
        except ValueError as e:
            raise requests.exceptions.RequestException(
                "Token endpoint returned non-JSON body"
            ) from e

        token = payload.get("token")
        if not token or not isinstance(token, str):
            message = (
                "No token was fetched — check credentials or server response format"
            )
            logger.error(message)
            raise KeyError(message)

        # Also update the token:
        self.token = token

        logger.info("Token fetched successfully")
        return token
