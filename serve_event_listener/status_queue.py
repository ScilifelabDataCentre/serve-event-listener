import logging
import os
import threading
import time
from datetime import datetime, timezone
from queue import Empty, Queue
from typing import Callable, Optional
from urllib.parse import urlparse

import requests

from serve_event_listener.el_types import PostPayload, StatusRecord
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
    Queue of StatusRecord items.
    Enriches with curl-probe and serializes to PostPayload just before sending.
    """

    # Attribute annotations (help static checkers)
    session: requests.Session
    url: str
    token: str
    token_fetcher: Optional[Callable[[], str]]
    prober: Optional[AppAvailabilityProbe]
    queue: Queue[StatusRecord]
    stop_event: threading.Event

    def __init__(
        self,
        session: requests.Session,
        url: str,
        token: str,
        token_fetcher: Optional[Callable[[], str]] = None,
        prober: Optional[AppAvailabilityProbe] = None,
    ):
        """
        Args:
            session: Shared HTTP session to reuse connections and settings.
            url: Absolute endpoint for posting status updates.
            token: Initial DRF token; may be refreshed via `token_fetcher`.
            token_fetcher: Optional callable returning a fresh token on 401/403.
            prober: Optional availability prober used before posting.

        Raises:
            ValueError: If `url` is not absolute or `token` is empty.
        """
        # Minimal runtime validation (nice for early misconfig)
        if not token:
            raise ValueError("token must be non-empty")
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"url must be absolute (got {url!r})")

        self.session = session
        self.url = url
        self.token = token
        self.token_fetcher = token_fetcher
        self.prober = prober

        self.queue = Queue[StatusRecord]()  # queue of StatusRecord items
        self.stop_event = threading.Event()

    def add(self, record: StatusRecord) -> None:
        """Enqueue a StatusRecord."""
        self.queue.put(record)
        logger.debug(
            "Added record for release=%s; q length now %s",
            record.get("release"),
            self.queue.qsize() + 1,
        )

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
                # Wait for 2 seconds
                rec: StatusRecord = self.queue.get(timeout=2)
                # status_data = self.queue.get(timeout=2)  # Wait for 2 seconds

                release = rec.get("release")
                new_status = rec.get("new-status")

                if new_status == "Deleted":
                    # TODO: use the new _maybe_probe_and_annotate function.
                    # If k8s status is deleted but probe says running, then return running
                    # otherwise keep the wait
                    logger.info(
                        "Processing release: %s. New status is Deleted!", release
                    )

                    event_ts = datetime.strptime(
                        rec.get("event-ts"), "%Y-%m-%dT%H:%M:%S.%fZ"
                    ).replace(tzinfo=timezone.utc)

                    if (datetime.now(timezone.utc) - event_ts).total_seconds() < 30:
                        # Deleted items are only processed after a duration has passed
                        # These events are put back in the queue and re-checked later
                        logger.debug(
                            "Less than 30 secs has passed. Waiting to process this Deleted event."
                        )
                        self.queue.put(rec)
                        # Also sleep a while
                        # Note that self.queue.qsize() is not reliable so we do not use it here
                        do_wait = True
                        continue

                elif new_status == "Running":
                    # TODO: check that the probe also confirms running, at least for shiny apps
                    # But add that logic to _maybe_probe_and_annotate so that non-running/deleted apps
                    # can be returned as normal
                    # Perhaps instead of hard-coding deleted and running we use the new APP_PROBE_STATUSES also here
                    pass
                else:
                    # Non-deleted: optionally probe
                    # TODO: handle the result
                    rec = self._maybe_probe_and_annotate(rec)

                payload = self._to_post_payload(rec)

                headers = {
                    "Authorization": f"Token {self.token}",
                    "Content-Type": "application/json",
                }
                resp = http_post(
                    self.session,
                    self.url,
                    data=payload,
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
            except Empty:
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

    def _maybe_probe_and_annotate(self, rec: StatusRecord) -> StatusRecord:
        """Optionally run availability probe and attach results."""
        if not self.prober or not APP_PROBE_STATUSES:
            return rec

        # TODO: Expect these keys from your StatusData
        app_type = (rec.get("app-type") or "").lower()
        new_status = (rec.get("new-status") or "").lower()
        app_url = rec.get("app-url")

        if (
            app_type not in APP_PROBE_APPS
            or new_status not in APP_PROBE_STATUSES
            or not app_url
        ):
            return rec

        pr = self.prober.probe_url(app_url)
        rec["curl-probe"] = {
            "status": pr.status,  # "Running" | "Unknown" | "NotFound"
            "port80_status": pr.port80_status,
            "note": pr.note,
            "url": app_url,
        }

        # (Optional) Override the status when it's clearly absent:
        # If k8s says Deleted and DNS says NotFound, we can skip the 30s delay.
        if new_status == "deleted" and pr.status == "NotFound":
            rec["probe-decision"] = "deleted-confirmed"
        elif new_status == "running" and pr.status in ("Unknown", "NotFound"):
            rec["probe-decision"] = "running-inconclusive"  # keep original, but mark

        return rec

    @staticmethod
    def _to_post_payload(rec: StatusRecord) -> PostPayload:
        """Build the API payload from a StatusRecord."""
        payload: PostPayload = {
            "release": rec["release"],
            "new-status": rec.get("new-status"),
            "event-ts": rec.get("event-ts"),
            "event-msg": {
                "pod-msg": rec.get("pod-msg"),
                "container-msg": rec.get("container-msg"),
            },
        }
        return payload
