import logging
import queue
import threading
import time
from datetime import datetime, timezone

import requests

from serve_event_listener.http_client import post as http_post

logger = logging.getLogger(__name__)


class StatusQueue:
    """
    StatusQueue represents a queue of k8s pod statuses to process.
    """

    def __init__(
        self, session: requests.Session, url: str, token: str, token_fetcher=None
    ):
        self.queue = queue.Queue()
        self.stop_event = threading.Event()

        self.session = session
        self.url = url
        self.token = token
        self.token_fetcher = token_fetcher

    def add(self, status_data) -> None:
        """Adds a status_data object to the queue."""
        logger.debug(
            "K8s release status data added to queue. Queue now has length %s",
            self.queue.qsize() + 1,
        )
        self.queue.put(status_data)

    def process(self) -> None:
        """Process the queue in a loop until a stop event is detected."""
        log_cnt_q_is_empty: int = 0
        do_wait: bool = False

        while not self.stop_event.is_set():
            if do_wait:
                do_wait = False
                time.sleep(4)

            try:
                # Get the event item at the front of the queue
                # This also removes it (pops) it from the queue
                status_data = self.queue.get(timeout=2)  # Wait for 2 seconds

                release = status_data["release"]
                new_status = status_data["new-status"]

                if new_status == "Deleted":
                    logger.info(
                        "Processing release: %s. New status is Deleted!", release
                    )

                    event_ts = datetime.strptime(
                        status_data["event-ts"], "%Y-%m-%dT%H:%M:%S.%fZ"
                    ).replace(tzinfo=timezone.utc)

                    if (datetime.now(timezone.utc) - event_ts).total_seconds() < 30:
                        # Deleted items are only processed after a duration has passed
                        # These events are put back in the queue and re-checked later
                        logger.debug(
                            "Less than 30 secs has passed. Waiting to process this Deleted event."
                        )
                        self.queue.put(status_data)
                        # Also sleep a while
                        # Note that self.queue.qsize() is not reliable so we do not use it here
                        do_wait = True
                        continue

                headers = {
                    "Authorization": f"Token {self.token}",
                    "Content-Type": "application/json",
                }
                resp = http_post(
                    self.session,
                    self.url,
                    data=status_data,
                    headers=headers,
                    token_fetcher=self.token_fetcher,
                )

                self.queue.task_done()

                if resp is None:
                    logger.warning(
                        "POST failed: network/transport error (wrapper returned None)"
                    )
                elif resp.status_code >= 400:
                    logger.warning(
                        "POST failed: status %s body=%s",
                        resp.status_code,
                        (resp.text or "")[:200],
                    )
                else:
                    logger.debug("POST ok: %s", resp.status_code)

                logger.debug(
                    "Processed queue successfully of release %s, new status=%s",
                    release,
                    new_status,
                )
                log_cnt_q_is_empty = 0
            except queue.Empty:
                if log_cnt_q_is_empty <= 2:
                    logger.debug("Nothing to do. The queue is empty.")
                elif log_cnt_q_is_empty == 3:
                    logger.debug(
                        "Nothing to do. The queue is empty. Suppressing this message for now."
                    )
                log_cnt_q_is_empty += 1
                # pass  # Continue looping if the queue is empty

    def stop_processing(self) -> None:
        """Stop processing the queue."""
        logger.warning("Queue processing stopped")
        self.stop_event.set()
