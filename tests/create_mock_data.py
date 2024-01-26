from datetime import datetime


def pod_basic_startup(release):
    statuses = ["waiting", "waiting", "running"]

    timestamps = [
        "2024-01-23T10:26:51Z",
        "2024-01-23T10:26:51Z",
        "2024-01-23T10:26:51Z",
    ]
    creation_timestamps = [
        datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ") for ts in timestamps
    ]

    # TODO: finish this
    pass


def pod_basic_delete(release):
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

    # TODO: finish this
    pass
