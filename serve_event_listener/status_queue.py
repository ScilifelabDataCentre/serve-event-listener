import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
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

# Probe (curl URL:s) behavior (seconds)
# _PROBE_WINDOW sets the duration during which probing will be retried
# _PROBE_INTERVAL sets the delay until the next probe retry
RUNNING_PROBE_WINDOW = 180
RUNNING_PROBE_INTERVAL = 10
DELETED_PROBE_WINDOW = 30
DELETED_PROBE_INTERVAL = 5


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
        """Process the queue in a loop until stop event is set.

        Probing is driven by config:
        - Only for statuses listed in APP_PROBE_STATUSES
        - Only for app types in APP_PROBE_APPS
        - Running (shiny/shiny-proxy): probe until confirmed (<=180s), then post
        - Deleted (shiny/shiny-proxy): confirm NotFound (<=30s), else time-out and post
        """
        log_cnt_q_is_empty = 0

        while not self.stop_event.is_set():
            try:
                # Get the event item at the front of the queue
                # This also removes it (pops) it from the queue
                # Wait for 2 seconds
                rec: StatusRecord = self.queue.get(timeout=2)
                release = rec.get("release")
                status_lc = (rec.get("new-status") or "").lower()

                posted = False
                requeue = False

                # Probing path (gated)
                if status_lc in {"running", "deleted"} and self._probe_enabled_for(rec):

                    logger.info(
                        "Processing release: %s. Preliminary new status is %s!",
                        release,
                        status_lc,
                    )

                    # Ensure we have a deadline for this record
                    # TODO: deadline = self._ensure_probe_deadline(rec)
                    deadline_epoch = rec.get("_probe-deadline-epoch")

                    now_epoch = time.time()
                    if deadline_epoch is not None and now_epoch < float(deadline_epoch):
                        # We are still within the probe window
                        if self._should_probe_now(rec):
                            # Try a probe attempt
                            app_url = rec.get("app-url")  # type: ignore[assignment]
                            pr = self.prober.probe_url(app_url)  # type: ignore[arg-type]
                            rec["curl-probe"] = {
                                "status": pr.status,  # "Running" | "Unknown" | "NotFound"
                                "port80_status": pr.port80_status,
                                "note": pr.note,
                                "url": app_url,
                            }

                            if status_lc == "deleted":

                                # TODO: check this
                                if pr.status == "NotFound":
                                    # Confirmed gone → post now
                                    pass  # proceed to POST
                                else:
                                    # Not yet gone → requeue until timeout
                                    requeue = True
                                    self._schedule_next_probe(rec)
                            elif status_lc == "running":

                                if pr.status == "Running":
                                    # Confirmed up → post now
                                    pass
                                else:
                                    # Not yet responsive → requeue until timeout
                                    requeue = True
                                    self._schedule_next_probe(rec)
                        else:
                            # Not time yet to probe; requeue to allow other items to proceed
                            requeue = True
                    else:
                        # Past deadline or no deadline → proceed to POST as-is (with any last probe info)
                        pass

                # Deleted default grace if probing is disabled for Deleted
                if (
                    not posted
                    and not requeue
                    and status_lc == "deleted"
                    and "deleted" not in APP_PROBE_STATUSES
                ):
                    # Keep existing <30s requeue behavior
                    event_ts = self._parse_event_ts(rec.get("event-ts"))
                    if event_ts:
                        if (self._utcnow() - event_ts).total_seconds() < 30:
                            requeue = True

                # TODO: Need to change Deleted logic, should always wait mandatory delay,
                # and for probed apps, should post Unknown if
                # Maybe deleted does not need to be probed except for at the end.
                # TODO: Modify test of deleted

                if requeue:
                    # Requeue and yield the CPU briefly
                    self.queue.task_done()
                    self.queue.put(rec)
                    time.sleep(min(RUNNING_PROBE_INTERVAL, DELETED_PROBE_INTERVAL, 0.5))
                    # time.sleep(min(PROBE_INTERVAL_SECONDS, 0.5))
                    continue

                # Build payload and POST
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
                        "POST failed: transport error (None) for release=%s", release
                    )
                elif resp.status_code >= 400:
                    # TODO: treat 404 and body containing "OK. OBJECT_NOT_FOUND." as not error.
                    logger.warning(
                        "POST failed: %s body=%s",
                        resp.status_code,
                        (resp.text or "")[:200],
                    )
                else:
                    logger.debug("POST ok: %s (%s)", resp.status_code, release)

                logger.debug(
                    "Processed queue successfully of release %s, new status=%s",
                    release,
                    status_lc,
                )
                log_cnt_q_is_empty = 0

            except Empty:
                if log_cnt_q_is_empty <= 2:
                    logger.debug("Nothing to do. The queue is empty.")
                elif log_cnt_q_is_empty == 3:
                    logger.debug("Nothing to do. Suppressing empty-queue logs.")
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

        # Optional decisions for callers to read
        if new_status == "deleted" and pr.status == "NotFound":
            rec["probe-decision"] = "deleted-confirmed"
        elif new_status == "running" and pr.status in ("Unknown", "NotFound"):
            rec["probe-decision"] = "running-inconclusive"

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

    # ---------- helpers for probing ----------

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _parse_event_ts(iso_ts: Optional[str]) -> Optional[datetime]:
        if not iso_ts:
            return None
        try:
            # Expecting "%Y-%m-%dT%H:%M:%S.%fZ"
            return datetime.strptime(iso_ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
                tzinfo=timezone.utc
            )
        except Exception:
            return None

    @staticmethod
    def _probe_deadline_seconds(app_type: str, status_lc: str) -> int:
        """Return max probe window in seconds for a (app_type, status)."""
        if app_type in {"shiny", "shiny-proxy"}:
            if status_lc == "running":
                return RUNNING_PROBE_WINDOW
            if status_lc == "deleted":
                return DELETED_PROBE_WINDOW
        return 0

    def _probe_enabled_for(self, rec: StatusRecord) -> bool:
        """Should we probe this record at all (per config + fields)?"""
        if not self.prober or not APP_PROBE_STATUSES:
            return False
        app_type = (rec.get("app-type") or "").lower()
        status_lc = (rec.get("new-status") or "").lower()
        app_url = rec.get("app-url")
        return (
            app_type in APP_PROBE_APPS
            and status_lc in APP_PROBE_STATUSES
            and bool(app_url)
        )

    def _ensure_probe_deadline(self, rec: StatusRecord) -> Optional[datetime]:
        """Ensure a per-record deadline exists; return it (or None if not applicable)."""
        app_type = (rec.get("app-type") or "").lower()
        status_lc = (rec.get("new-status") or "").lower()
        window = self._probe_deadline_seconds(app_type, status_lc)
        if window <= 0:
            return None

        # Determine start point: event-ts or now if absent
        started_at = self._parse_event_ts(rec.get("event-ts")) or self._utcnow()
        deadline = started_at + timedelta(seconds=window)

        # Store as ISO for diagnostics and as epoch for quick comparisons
        if "_probe-deadline-iso" not in rec:
            rec["_probe-deadline-iso"] = deadline.isoformat()
        rec["_probe-deadline-epoch"] = deadline.timestamp()
        return deadline

    def _should_probe_now(self, rec: StatusRecord) -> bool:
        """Throttle probe attempts; true if it's time to try again."""
        next_epoch = rec.get("_probe-next-epoch")
        now = time.time()
        return (next_epoch is None) or (now >= float(next_epoch))

    def _schedule_next_probe(self, rec: StatusRecord) -> None:
        rec["_probe-next-epoch"] = time.time() + 1  # TODO: PROBE_INTERVAL_SECONDS
