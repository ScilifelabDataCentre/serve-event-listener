import logging
from datetime import datetime, timezone

from kubernetes.client import models

from serve_event_listener.status_data import StatusData

logger = logging.getLogger(__name__)


class Pod(models.V1Pod):
    def __init__(self):
        super().__init__()
        self.created = False
        self.deleted = False

    def create(self, release: str):
        if not self.deleted:
            self.created = True
            waiting = models.V1ContainerStateWaiting(reason="ContainerCreating")

            state = models.V1ContainerState(waiting=waiting)

            container_status = models.V1ContainerStatus(
                name="name",
                state=state,
                image="some image",
                image_id="123",
                ready=False,
                restart_count=0,
            )

            status = models.V1PodStatus(container_statuses=[container_status])

            metadata = models.V1ObjectMeta(
                creation_timestamp=self.get_current_time(),
                deletion_timestamp=None,
                labels={"release": release},
            )

            self.status = status
            self.metadata = metadata
        else:
            print("Pod is deleted - Not possible to create")

    def delete(self):
        if self.created:
            self.metadata.deletion_timestamp = self.get_current_time()

            # TODO: check these reasons
            terminated = models.V1ContainerStateTerminated(
                reason="Terminated", message="Deleted", exit_code=1
            )

            state = models.V1ContainerState(
                terminated=terminated,
            )

            self.status.container_statuses[0].state = state
        else:
            print("Pod is not created yet")

    def running(self):
        if self.created and not self.deleted:
            running = models.V1ContainerStateRunning(started_at=self.get_current_time())
            state = models.V1ContainerState(
                running=running,
            )

            self.status.container_statuses[0].state = state
            self.status.container_statuses[0].ready = True

        else:
            print("Pod is not created yet")

    def error_image_pull(self):
        if self.created and not self.deleted:
            waiting = models.V1ContainerStateWaiting(
                message="Some message", reason="ErrImagePull"
            )
            state = models.V1ContainerState(
                waiting=waiting,
            )
            self.status.container_statuses[0].state = state
            self.status.container_statuses[0].ready = False
        else:
            print("Pod not created yet")

    def get_current_time(self):
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
