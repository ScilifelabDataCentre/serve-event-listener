import unittest

from serve_event_listener.status_data import StatusData
from tests.create_pods import Pod


class TestPodProcessing(unittest.TestCase):
    release = "some_release"

    def setUp(self) -> None:
        self.pod = Pod()
        self.status_data = StatusData()

    def test_pod_startup(self):
        release = "r1234567"
        self.pod.create(release)
        event = {"object": self.pod}
        self.status_data.update(event)

        assert self.status_data.status_data[release].get("status") == "Created"

    def test_pod_delete(self):
        release = "r1234567"
        self.pod.create(release)

        event = {"object": self.pod}
        self.status_data.update(event)

        assert self.status_data.status_data[release].get("status") == "Created"
        self.pod.delete()

        event = {"object": self.pod}
        self.status_data.update(event)

        assert self.status_data.status_data[release].get("status") == "Deleted"

    def test_pod_running(self):
        release = "r1234567"

        self.pod.create(release)
        self.status_data.update({"object": self.pod})

        assert self.status_data.status_data[release].get("status") == "Created"

        self.pod.running()
        self.status_data.update({"object": self.pod})
        assert self.status_data.status_data[release].get("status") == "Running"

    def test_failed_image_pull(self):
        release = "r1234567"

        self.pod.create(release)
        self.status_data.update({"object": self.pod})
        self.assertEqual(self.status_data.status_data[release].get("status"), "Created")

        self.pod.error_image_pull()
        self.status_data.update({"object": self.pod})
        self.assertEqual(
            self.status_data.status_data[release].get("status"), "Image Error"
        )

    def test_replica_scenario(self):
        """
        This scenario creates a pod, then creates a new pod.
        After the second pod is created, the first one is deleted.
        This is similar to how replicasets work"""

        release = "r1234567"

        self.pod.create(release)
        self.status_data.update({"object": self.pod})

        self.new_pod = Pod()
        self.new_pod.create(release)
        self.status_data.update({"object": self.new_pod})

        self.pod.delete()
        self.status_data.update({"object": self.pod})

        self.new_pod.running()
        self.status_data.update({"object": self.new_pod})

        self.assertEqual(self.status_data.status_data[release].get("status"), "Running")

        self.new_pod.delete()
        self.status_data.update({"object": self.new_pod})

        self.assertEqual(self.status_data.status_data[release].get("status"), "Deleted")


if __name__ == "__main__":
    unittest.main()
