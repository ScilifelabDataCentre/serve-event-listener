from datetime import datetime
from typing import Any, Dict, Tuple, Union

from serve_event_listener.utils import update_or_create_status


def pod_basic_startup(release: str) -> dict:
    statuses = ["waiting", "waiting", "running"]

    timestamps = [
        "2024-01-23T10:26:51Z",
        "2024-01-23T10:26:51Z",
        "2024-01-23T10:26:51Z",
    ]
    creation_timestamps = [
        datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ") for ts in timestamps
    ]

    status_data: Dict[Any, Any] = {}
    for status, ts in zip(statuses, creation_timestamps):
        status_data = update_or_create_status(
            status_data, status, release, ts, deletion_timestamp=None
        )

    return status_data


def pod_basic_delete(release: str) -> dict:
    statuses = ["waiting", "waiting", "running"]

    creation_timestamps_strings = [
        "2024-01-23T10:26:51Z",
        "2024-01-23T10:26:51Z",
        "2024-01-23T10:26:51Z",
    ]

    deletion_timestamp_strings = [
        None,
        None,
        "2024-01-23T10:26:52Z",
    ]

    creation_timestamps = [
        datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
        for ts in creation_timestamps_strings
    ]
    deletion_timestamps = [
        datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ") if ts else None
        for ts in deletion_timestamp_strings
    ]

    status_data: Dict[Any, Any] = {}
    for status, creation_timestamp, deletion_timestamp in zip(
        statuses, creation_timestamps, deletion_timestamps
    ):
        status_data = update_or_create_status(
            status_data, status, release, creation_timestamp, deletion_timestamp
        )

    return status_data
