import logging
import time

import urllib3
from kubernetes import watch
from serve_event_listener._utils import (convert_to_post_data, get_token, get_url, post,
                   setup_client, update_status_data)

logger = logging.getLogger(__name__)

TOKEN_API_ENDPOINT = "/api/token-auth/"
APP_STATUS_API_ENDPOINT = "/apps/"


def init(**kwargs):
    """
    The event listener takes the latest event and checks if a corresponding appinstance
    should be updated. It uses the creation timestamp to always use the status of the youngest pod
    in a helm release.
    """
    max_retries = kwargs.get("max_retries", 10)
    retry_delay = kwargs.get("retry_delay", 2)

    namespace = kwargs.get("namespace", "default")
    label_selector = kwargs.get("label_selector", "type=app")

    base_url = kwargs.get("base_url")
    username = kwargs.get("username")
    password = kwargs.get("password")

    auth_data = {"username": username, "password": password}
    token = get_token(base_url + TOKEN_API_ENDPOINT, auth_data)

    retries = 0

    while retries < max_retries:
        try:
            k8s_api = setup_client()
            k8s_watch = watch.Watch()

            start_stream(k8s_watch, k8s_api, namespace, label_selector, base_url, token)

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


def start_stream(k8s_watch, k8s_api, namespace, label_selector, url, token):
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

        release = max(status_data, key=lambda k: status_data[k]["event-ts"])

        if not status_data[release]["sent"]:
            post_data = convert_to_post_data(status_data, release)

            # status_code = requests.get(url=url, verify=False)
            status_code = post(
                url=url + APP_STATUS_API_ENDPOINT, token=token, data=post_data
            )
            if status_code == 200:
                status_data[release]["sent"] = True

    return False



import queue
import requests
import threading
import time

class EventProcessor:
    def __init__(self):
        self.request_queue = queue.Queue()
        self.stop_event = threading.Event()

    def process_event(self, event_data):
        # Simulate processing event data and sending a request
        time.sleep(2)  # Simulating delay in processing
        response = requests.post("https://example.com/api/update", data=event_data)
        print(f"Processed event data: {event_data}, Response: {response.text}")

    def add_to_queue(self, event_data):
        self.request_queue.put(event_data)

    def process_queue(self):
        while not self.stop_event.is_set():
            try:
                event_data = self.request_queue.get(timeout=1)  # Wait for 1 second
                self.process_event(event_data)
                self.request_queue.task_done()
            except queue.Empty:
                pass  # Continue looping if the queue is empty

    def stop_processing(self):
        self.stop_event.set()

# Example usage

event_processor = EventProcessor()

# Start a thread to process the queue
processing_thread = threading.Thread(target=event_processor.process_queue)
processing_thread.start()

try:
    # Simulate events being added to the queue in the main program stream loop
    for event_number in range(5):
        event_data = {
            "temperature": event_number + 20,
            "pressure": event_number + 1000,
            "time": time.time()
        }
        event_processor.add_to_queue(event_data)
        time.sleep(1)  # Simulate waiting for the next event

    # Wait for all events to be processed
    event_processor.request_queue.join()

finally:
    # Stop the processing thread when done
    event_processor.stop_processing()
    processing_thread.join()