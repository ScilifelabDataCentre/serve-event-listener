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

# Confirm Deleted only after N consecutive NXDOMAINs (NotFound)
NXDOMAIN_CONFIRMATION_COUNT = int(os.getenv("APP_PROBE_NXDOMAIN_CONFIRM", "2"))


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

        Rules:
          - Probing is fully gated by APP_PROBE_STATUSES and APP_PROBE_APPS.
          - Running (shiny/shiny-proxy): confirm 'Running' within RUNNING_PROBE_WINDOW/INTERVAL.
          - Deleted (shiny/shiny-proxy): require NXDOMAIN NotFound N times within
            DELETED_PROBE_WINDOW/INTERVAL, then confirm 'Deleted'.
          - If probing is disabled for 'deleted', keep legacy <30s requeue grace.
        """
        # Track logging of empty queue
        q_empty_log = 0

        while not self.stop_event.is_set():
            try:
                # get the event item at the front of the queue.
                # this also removes it (pops) it from the queue.
                # wait for 2 seconds.
                rec: StatusRecord = self.queue.get(timeout=2)
                release = rec.get("release")
                status_lc = (rec.get("new-status") or "").lower()

                # default action: proceed to POST (may be flipped to requeue)
                requeue = False

                # probing path (gated)
                if status_lc in {"running", "deleted"} and self._probe_enabled_for(rec):

                    logger.info(
                        "Processing release: %s. Preliminary new status is %s!",
                        release,
                        status_lc,
                    )

                    deadline_epoch = self._ensure_deadline(rec, status_lc)
                    if deadline_epoch is not None and time.time() < deadline_epoch:
                        # still inside the probe window
                        if self._allow_probe_now(rec):
                            # try a probe attempt
                            app_url = rec.get("app-url")  # type: ignore[assignment]
                            pr = self.prober.probe_url(app_url)  # type: ignore[arg-type]
                            rec["curl-probe"] = {
                                "status": pr.status,  # "Running" | "Unknown" | "NotFound"
                                "port80_status": pr.port80_status,
                                "note": pr.note,
                                "url": app_url,
                            }

                            if status_lc == "running":
                                # confirm only on probe Running; otherwise keep probing
                                if pr.status != "Running":
                                    requeue = True
                                    self._schedule_next_probe(rec, status_lc)
                            else:  # deleted
                                # Deleted only confirms on NXDOMAIN NotFound; require N consecutive
                                if pr.status == "NotFound":
                                    nx = int(rec.get("_nx_consec", 0)) + 1
                                    rec["_nx_consec"] = nx
                                    if nx < NXDOMAIN_CONFIRMATION_COUNT:
                                        requeue = True
                                        self._schedule_next_probe(rec, status_lc)
                                else:
                                    # reset consecutive counter and keep probing
                                    if "_nx_consec" in rec:
                                        rec["_nx_consec"] = 0
                                    requeue = True
                                    self._schedule_next_probe(rec, status_lc)
                        else:
                            # not time yet; requeue to avoid blocking
                            requeue = True
                    # else: past deadline → proceed to POST as-is

                # legacy grace for Deleted when probing is disabled for Deleted
                if (
                    status_lc == "deleted"
                    and "deleted" not in APP_PROBE_STATUSES
                    and not requeue
                ):
                    evt = self._parse_event_ts(rec.get("event-ts"))
                    if evt and (self._utcnow() - evt).total_seconds() < 30:
                        requeue = True

                if requeue:
                    self.queue.task_done()
                    self.queue.put(rec)
                    # yield briefly; actual delay is handled by the next-epoch throttle
                    time.sleep(0.01)
                    continue

                # build payload and POST
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
                        "POST failed: transport error (None) release=%s", release
                    )
                elif resp.status_code >= 400:
                    body = resp.text or ""
                    if resp.status_code == 404 and "OK. OBJECT_NOT_FOUND." in body:
                        logger.debug(
                            "Response 404 treated as OK (object not found). Could be renamed or removed. release=%s body=%s",
                            release,
                            body[:200],
                        )
                    else:
                        logger.warning(
                            "POST failed: %s body=%s",
                            resp.status_code,
                            body[:200],
                        )
                else:
                    logger.debug("POST ok: %s (%s)", resp.status_code, release)

                logger.debug(
                    "Processed queue successfully of release %s, new status=%s",
                    release,
                    status_lc,
                )
                q_empty_log = 0

            except Empty:
                if q_empty_log <= 2:
                    logger.debug("Nothing to do. The queue is empty.")
                elif q_empty_log == 3:
                    logger.debug("Nothing to do. Suppressing empty-queue logs.")
                q_empty_log += 1
                # pass, continue looping if the queue is empty

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
            return datetime.strptime(iso_ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
                tzinfo=timezone.utc
            )
        except Exception:
            return None

    @staticmethod
    def _probe_cfg_for(status_lc: str) -> tuple[int, float]:
        """Return (window_seconds, interval_seconds) for a status."""
        if status_lc == "running":
            return RUNNING_PROBE_WINDOW, float(RUNNING_PROBE_INTERVAL)
        if status_lc == "deleted":
            return DELETED_PROBE_WINDOW, float(DELETED_PROBE_INTERVAL)
        return 0, 0.0

    def _probe_enabled_for(self, rec: StatusRecord) -> bool:
        """Only probe when config & fields allow it."""
        if not self.prober or not APP_PROBE_STATUSES:
            return False
        app_type = (rec.get("app-type") or "").lower()
        status_lc = (rec.get("new-status") or "").lower()
        app_url = rec.get("app-url")
        return (
            status_lc in APP_PROBE_STATUSES
            and app_type in APP_PROBE_APPS
            and bool(app_url)
        )

    def _ensure_deadline(self, rec: StatusRecord, status_lc: str) -> Optional[float]:
        """Ensure a per-record deadline epoch is stored; return it (or None if not applicable)."""
        window, _ = self._probe_cfg_for(status_lc)
        if window <= 0:
            return None
        if "_probe-deadline-epoch" in rec:
            return float(rec["_probe-deadline-epoch"])  # type: ignore[index]
        started_at = self._parse_event_ts(rec.get("event-ts")) or self._utcnow()
        deadline = started_at + timedelta(seconds=window)
        rec["_probe-deadline-epoch"] = deadline.timestamp()  # transient
        return float(rec["_probe-deadline-epoch"])  # type: ignore[index]

    def _allow_probe_now(self, rec: StatusRecord) -> bool:
        """Throttle attempts via _probe-next-epoch (transient)."""
        next_epoch = rec.get("_probe-next-epoch")
        return (next_epoch is None) or (time.time() >= float(next_epoch))

    def _schedule_next_probe(self, rec: StatusRecord, status_lc: str) -> float:
        """Set the next time we may probe this record, per status interval."""
        _, interval = self._probe_cfg_for(status_lc)
        next_epoch = time.time() + max(0.0, float(interval))
        # mutate the record in place by setting a transient field
        rec["_probe-next-epoch"] = next_epoch
        # also return it for convenience
        return next_epoch
