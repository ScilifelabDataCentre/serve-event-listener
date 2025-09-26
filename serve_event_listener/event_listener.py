import logging
import os
import threading
import time
from typing import Any, Optional, Union

import requests
import urllib3
from kubernetes import client, config, watch
from kubernetes.client.exceptions import ApiException
from urllib3.exceptions import HTTPError

from serve_event_listener.http_client import get as http_get
from serve_event_listener.http_client import make_session
from serve_event_listener.http_client import post as http_post
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

        self.setup_complete = False
        self._status_data = StatusData()
        self._status_queue = None
        self._prober = None
        self.token = None

        # Track the latest resourceVersion
        self.resource_version = None

        # Prepare session and settings for http get and post requests
        self.session = make_session(total_retries=3)
        self.token_fetcher = self.fetch_token
        self.verify_tls = False

        # Same settings as in http client but repeated for clarity
        self.timeout = (3.05, 20.0)
        self.backoff_seconds = (1, 2, 4)

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
                verify_tls=self.verify_tls,
                timeout=self.timeout,
                backoff_seconds=self.backoff_seconds,
            )

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
                        timeout_seconds=240,
                    ):
                        # Update resource_version to latest
                        self.resource_version = event[
                            "object"
                        ].metadata.resource_version

                        # Update status_data_object with new event
                        self.status_data.update(event)

                        record = self.status_data.get_status_record()

                        # TODO: If you know the per-release URL here, add it now so the probe can use it:
                        # record["app-url"] = app_url_resolver(record["release"], ...)

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
        url = BASE_URL + "/openapi/v1/are-you-there"
        logger.debug("Verifying that the server API is up and available via %s", url)

        # Using the new http get function
        response = http_get(
            self.session,
            url,
            verify=self.verify_tls,
            timeout=self.timeout,
            backoff_seconds=self.backoff_seconds,
        )
        # response = self.get(url=BASE_URL + "/openapi/v1/are-you-there")

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
            timeout_seconds=120,
            watch=False,
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
                timeout_seconds=120,
                watch=False,
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

        # Use the shared session and your central call options.
        # NOTE: token_fetcher=None — we don't want the client to recurse to fetch a token
        # while we're already fetching one.
        response: Optional[requests.Response] = http_post(
            self.session,
            TOKEN_API_ENDPOINT,
            data=data,
            verify=self.verify_tls,
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

    def post(
        self,
        url: str = APP_STATUS_API_ENDPOINT,
        data: dict = {},
        headers: Union[None, dict] = None,
    ):
        """
        Send a POST request to the specified URL with the provided data and token.

        Args:
            url (str): The URL to send the POST request to.
            data (dict): The data to be included in the POST request.
            header (None or dict): header for the request.

        Returns:
            int: The HTTP status code of the response.
        """

        # TODO: Deprecate for now. Later can be removed.
        raise DeprecationWarning("Deprecated function. To be removed.")

        logger.debug("POST called to URL %s", url)
        try:
            for sleep in [1, 2, 4]:
                # Use connect timeout as 3.05s and read timeout of 20s
                response = requests.post(
                    url=url,
                    json=data,
                    headers=headers,
                    verify=False,
                    timeout=(3.05, 20),
                )
                status_code = response.status_code

                if status_code == 200:
                    logger.info("Successful POST - Returned 200 - %s", response.text)
                    break

                elif status_code == 400:
                    logger.warning("Failed POST - Returned 400")
                    break

                elif status_code in [401, 403]:
                    logger.warning(
                        "Received status code %s - Fetching new token and retrying once",
                        status_code,
                    )
                    self.token = self.fetch_token()
                    self._status_queue.token = self.token

                    # Retry once
                    time.sleep(sleep)
                    if sleep > 1:
                        break

                elif status_code in [404]:
                    logger.warning(
                        "Received status code %s - %s", status_code, response.text
                    )
                    break

                elif str(status_code).startswith("5"):
                    logger.warning("Received status code %s", status_code)
                    logger.warning("Retrying in %s seconds", sleep)
                    time.sleep(sleep)

                else:
                    logger.warning("Received uncaught status code: %s", status_code)

            logger.info("POST returned - Status code: %s", status_code)

        except requests.exceptions.ConnectTimeout as e:
            logger.warning("Unable to POST to server. ConnectTimeout: %s", e)
            response = None

        except requests.exceptions.ReadTimeout as e:
            logger.warning(
                "Unable to read response from POST to server. ReadTimeout: %s", e
            )
            response = None

        except requests.exceptions.Timeout as e:
            logger.warning("Timeout while POST-ing to server: %s", e)
            response = None

        except requests.exceptions.ConnectionError as e:
            logger.warning("Unable to POST to server. ConnectionError %s", e)
            response = None

        except requests.exceptions.RequestException as e:
            logger.warning("Error during POST-ing to server. RequestException: %s", e)
            response = None

        return response

    def get(self, url: str, headers: Union[None, dict] = None):
        """
        Send a GET request to the specified URL with the provided data and token.

        Args:
            url (str): The URL to send the GET request to.
            data (dict): The data to be included in the POST request.
            header (None or dict): header for the request.

        Returns:
            int: The HTTP status code of the response.
        """

        # TODO: Deprecate for now. Later can be removed.
        raise DeprecationWarning("Deprecated function. To be removed.")

        try:
            # Use connect timeout as 3.05s and read timeout of 20s
            response = requests.get(
                url=url, headers=headers, verify=False, timeout=(3.05, 20)
            )
            logger.info("GET returned status code: %s", response.status_code)

        except requests.exceptions.ConnectTimeout as e:
            logger.warning("Unable to GET to server. ConnectTimeout: %s", e)
            response = None

        except requests.exceptions.ReadTimeout as e:
            logger.warning(
                "Unable to read response from GET to server. ReadTimeout: %s", e
            )
            response = None

        except requests.exceptions.Timeout as e:
            logger.warning("Timeout while GET-ing to server: %s", e)
            response = None

        except requests.exceptions.ConnectionError as e:
            logger.warning("Unable to GET to server. ConnectionError %s", e)
            response = None

        except requests.exceptions.RequestException as e:
            logger.warning("Error during GET-ing to server. RequestException: %s", e)
            response = None

        return response
