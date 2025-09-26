import logging
import os
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests

from serve_event_listener.http_client import post as http_post
from serve_event_listener.probing import AppAvailabilityProbe

logger = logging.getLogger(__name__)

# Parse the env variable used by status probing
ALLOWED_STATUSES = {"running", "deleted", "pending", "created", "error", "unknown"}


def _parse_csv_env(name: str) -> set[str]:
    raw = os.getenv(name, "") or ""
    items = {s.strip().lower() for s in raw.split(",") if s.strip()}
    if not items or items & {"none", "off"}:
        return set()  # disabled
    return items & ALLOWED_STATUSES


APP_PROBE_STATUSES = _parse_csv_env("APP_PROBE_STATUSES")  # e.g. "Running,Deleted"
APP_PROBE_APPS = {
    s.strip().lower()
    for s in (os.getenv("APP_PROBE_APPS", "shiny,shiny-proxy")).split(",")
}


class StatusQueue:
    """
    StatusQueue represents a queue of k8s pod statuses to process.
    """

    def __init__(
        self,
        session: requests.Session,
        url: str,
        token: str,
        token_fetcher=None,
        prober: Optional[AppAvailabilityProbe] = None,
    ):
        self.queue = queue.Queue()
        self.stop_event = threading.Event()

        self.session = session
        self.url = url
        self.token = token
        self.token_fetcher = token_fetcher
        self.prober = prober

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
                    # TODO: use the new _maybe_probe_and_annotate function.
                    # If k8s status is deleted but probe says running, then return running
                    # otherwise keep the wait
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

                elif new_status == "Running":
                    # TODO: check that the probe also confirms running, at least for shiny apps
                    # But add that logic to _maybe_probe_and_annotate so that non-running/deleted apps can be returned as normal
                    # Perhaps instead of hard-coding deleted and running we use the new APP_PROBE_STATUSES also here
                    pass
                else:
                    # Non-deleted: optionally probe
                    # TODO: handle the result
                    status_data = self._maybe_probe_and_annotate(status_data)

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

    def _maybe_probe_and_annotate(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Optionally run availability probe and attach results."""
        if not self.prober or not APP_PROBE_STATUSES:
            return item

        # Expect these keys from your StatusData -> get_post_data()
        app_type = (item.get("app-type") or "").lower()
        new_status = (item.get("new-status") or "").lower()
        app_url = item.get("app-url")  # caller supplies per-app URL

        if (
            app_type not in APP_PROBE_APPS
            or new_status not in APP_PROBE_STATUSES
            or not app_url
        ):
            return item

        # TODO: item struct must match status data, maybe a common function or dataclass for this
        pr = self.prober.probe_url(app_url)
        item["curl-probe"] = {
            "status": pr.status,  # "Running" | "Unknown" | "NotFound"
            "port80_status": pr.port80_status,
            "note": pr.note,
            "url": app_url,
        }

        # (Optional) Override the status when it's clearly absent:
        # If k8s says Deleted and DNS says NotFound, we can skip the 30s delay.
        if new_status == "deleted" and pr.status == "NotFound":
            item["probe-decision"] = "deleted-confirmed"
        elif new_status == "running" and pr.status in ("Unknown", "NotFound"):
            item["probe-decision"] = "running-inconclusive"  # keep original, but mark

        return item
