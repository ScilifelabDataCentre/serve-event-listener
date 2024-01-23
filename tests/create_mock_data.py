from datetime import datetime
from typing import Dict, Mapping, Union

from serve_event_listener.utils import create_status_data_dict


def pod_start_event_generator():
    pass


"""namespace = "default"
    release = "r123f991"

    readiness = [False, False, True]
    states = ["waiting", "waiting", "running"]
    reasons = ["ContainerCreating", "ContainerCreating", None]
    messages = [None, None, None]
    creation_timestamps = [
        "2024-01-23T10:26:51Z",
        "2024-01-23T10:26:51Z",
        "2024-01-23T10:26:51Z",
    ]
    status_data = {}"""

# status_data = create_status_data_dict(status_data, status, release, creation_timestamp, deletion_timestamp=None)
