import logging
import os
import queue
import threading

import requests

logger = logging.getLogger(__name__)


class StatusQueue:
    def __init__(self, post_handler, token):
        self.queue = queue.Queue()
        self.stop_event = threading.Event()

        # The post handler is a function that is set by the EventListener class
        self.post_handler = post_handler
        self.token = token

    def add(self, status_data):
        logger.debug(
            f"Data added to queue. Queue now has length {self.queue.qsize()+1}"
        )
        self.queue.put(status_data)

    def process(self):
        while not self.stop_event.is_set():
            try:
                status_data = self.queue.get(timeout=2)  # Wait for 1 second

                self.post_handler(
                    data=status_data,
                    headers={"Authorization": f"Token {self.token}"},
                )

                self.queue.task_done()

                logger.debug("Processing queue successfully")
            except queue.Empty:
                pass  # Continue looping if the queue is empty

    def stop_processing(self):
        logger.warning("Queue processing stopped")
        self.stop_event.set()
