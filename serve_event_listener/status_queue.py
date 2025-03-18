import logging
import queue
import threading
import time
from datetime import datetime, timezone

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
                        f"Processing release: {release}. New status is Deleted!"
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

                self.post_handler(
                    data=status_data,
                    headers={"Authorization": f"Token {self.token}"},
                )

                self.queue.task_done()

                logger.debug(
                    f"Processed queue successfully of release {release}, new status={new_status}"
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

    def stop_processing(self):
        logger.warning("Queue processing stopped")
        self.stop_event.set()
