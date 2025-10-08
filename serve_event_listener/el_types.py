"""Defines common data types"""

from typing import Any, Literal, Mapping, NotRequired, Optional, TypedDict

# Canonical status/app types
Status = Literal["Running", "Pending", "Deleted", "Failed", "Unknown", "Succeeded"]
AppType = Literal["shiny", "shiny-proxy"]
ProbeStatus = Literal["Running", "Unknown", "NotFound"]

# Result block produced by the AppAvailabilityProbe (optional on payload)
ProbeBlockDict = TypedDict(
    "ProbeBlockDict",
    {
        "status": ProbeStatus,  # e.g., "Running" | "Unknown" | "NotFound"
        "port80_status": Optional[int],  # HTTP status code (if any)
        "note": str,  # short diagnostic
        "url": str,  # probed URL
    },
    total=False,
)

# Internal, per-release record kept by StatusData (snake-ish keys ok here)
StatusRecord = TypedDict(
    "StatusRecord",
    {
        "release": str,
        "status": Status,
        "event-ts": str,  # ISO8601 "YYYY-mm-ddTHH:MM:SS.sssZ"
        "namespace": NotRequired[str],
        "resource_version": NotRequired[str],
        "pod": NotRequired[str],  # current pod name (if relevant)
        "labels": NotRequired[Mapping[str, str]],
        "reason": NotRequired[str],
        "message": NotRequired[str],
        "app-type": NotRequired[AppType],
        "app-url": NotRequired[str],
        "curl-probe": NotRequired[ProbeBlockDict],
    },
    total=False,
)

# External payload for POST-ing
EventMsg = TypedDict(
    "EventMsg",
    {
        "pod-msg": Optional[str],
        "container-msg": Optional[str],
    },
    total=True,
)

PostPayload = TypedDict(
    "PostPayload",
    {
        "release": str,
        "new-status": Status,
        "event-msg": EventMsg,
        "event-ts": Optional[str],  # ISO8601 or None
        "token": str,
    },
    total=False,
)


def validate_status_record(rec: Mapping[str, Any]) -> None:
    """Lightweight runtime guard for required fields (raises ValueError)."""
    missing = [k for k in ("release", "status", "event-ts") if k not in rec]
    if missing:
        raise ValueError(f"StatusRecord missing required fields: {missing}")
