import logging
import os
import sys
import time
import unittest

from serve_event_listener.status_data import StatusData
from tests.create_pods import Pod, PodStatus

# Setup logging output for unit test execution
DEBUG = os.getenv("DEBUG", default="True").lower() in ("true", "1", "t")
# Set up logging configuration with the ColoredFormatter
if DEBUG:
    level = logging.DEBUG
else:
    level = logging.INFO

TEST_LOG_STREAM = os.environ.get("TEST_LOG_STREAM", None)
if TEST_LOG_STREAM and eval(TEST_LOG_STREAM) is not None:
    logging.basicConfig(stream=eval(TEST_LOG_STREAM), level=level)
else:
    logging.basicConfig(handlers=[logging.NullHandler()], level=level)


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

        assert (
            self.status_data.status_data[release].get("status") == "ContainerCreating"
        )  # before: Created

    def test_pod_delete(self):
        release = "r1234567"
        self.pod.create(release)

        event = {"object": self.pod}
        self.status_data.update(event)

        assert (
            self.status_data.status_data[release].get("status") == "ContainerCreating"
        )  # before: Created

        time.sleep(0.01)

        self.pod.delete()

        event = {"object": self.pod}
        self.status_data.update(event)

        assert (
            self.status_data.status_data[release].get("status") == "Terminated"
        )  # before: Deleted

    def test_pod_running(self):
        release = "r1234567"

        self.pod.create(release)
        self.status_data.update({"object": self.pod})

        assert (
            self.status_data.status_data[release].get("status") == "ContainerCreating"
        )  # before: Created

        self.pod.running()
        self.status_data.update({"object": self.pod})
        assert self.status_data.status_data[release].get("status") == "Running"

    def test_failed_image_pull(self):
        release = "r1234567"

        self.pod.create(release)
        self.status_data.update({"object": self.pod})
        self.assertEqual(
            self.status_data.status_data[release].get("status"), "ContainerCreating"
        )  # before: Created

        self.pod.error_image_pull()
        self.status_data.update({"object": self.pod})
        self.assertEqual(
            # self.status_data.status_data[release].get("status"), "Image Error"
            self.status_data.status_data[release].get("status"),
            "ErrImagePull",
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

        time.sleep(0.01)

        self.pod.delete()
        self.status_data.update({"object": self.pod})

        time.sleep(0.01)

        self.new_pod.running()
        self.status_data.update({"object": self.new_pod})

        self.assertEqual(self.status_data.status_data[release].get("status"), "Running")

        time.sleep(0.01)

        self.new_pod.delete()
        self.status_data.update({"object": self.new_pod})

        self.assertEqual(
            self.status_data.status_data[release].get("status"), "Terminated"
        )  # before: Deleted

    @unittest.skip(
        "This test no longer works after we rely on k8s truth for deletions."
    )
    def test_valid_and_invalid_image_edits(self):
        """
        This scenario creates a pod, then creates a pod with an invalid image, and finally
        it creates a pod with a valid image.
        After the third pod is created, the first two are deleted.
        Finally the valid pod is also deleted.
        This occurs when a user changes the image to an invalid image and then valid image.
        """

        # TODO: Consider re-enabling this test by for example creating a parallel data structure
        # containing a list of k8s pods and statuses.

        release = "r-valid-invalid-images"

        # Pod: pod
        self.pod.create(release)
        self.status_data.update({"object": self.pod})

        assert (
            self.status_data.status_data[release].get("status") == "ContainerCreating"
        )

        time.sleep(0.01)

        self.pod.running()
        self.status_data.update({"object": self.pod})
        assert self.status_data.status_data[release].get("status") == "Running"

        # Pod: invalid_pod
        self.invalid_pod = Pod()
        self.invalid_pod.create(release)

        time.sleep(0.01)

        self.invalid_pod.error_image_pull()
        self.status_data.update({"object": self.invalid_pod})
        assert (
            self.status_data.status_data[release].get("status") == "ErrImagePull"
        )  # before: Image Error

        # Now there are two pods in the release, one older Running and one newer with ErrImagePull

        # Pod: valid_pod
        self.valid_pod = Pod()
        self.valid_pod.create(release)

        time.sleep(0.01)

        self.valid_pod.running()
        self.status_data.update({"object": self.valid_pod})
        assert self.status_data.status_data[release].get("status") == "Running"

        # The first two pods are deleted but the last pod should remain running
        self.pod.delete()
        self.invalid_pod.delete()

        self.status_data.update({"object": self.pod})

        msg = f"Release created ts={self.status_data.status_data[release].get("creation_timestamp")}, \
                deleted ts={self.status_data.status_data[release].get("deletion_timestamp")}"

        print(msg)

        self.assertEqual(
            self.status_data.status_data[release].get("status"),
            "Running",
            f"Release should be Running after delete of first pod, \
                         ts pod deleted={self.pod.metadata.deletion_timestamp} vs \
                         ts invalid_pod deleted={self.invalid_pod.metadata.deletion_timestamp} vs \
                         ts valid_pod created={self.valid_pod.metadata.creation_timestamp}, {msg}",
        )

        self.status_data.update({"object": self.invalid_pod})
        self.assertEqual(
            self.status_data.status_data[release].get("status"),
            "Running",
            f"Release should be Running after delete of 2nd invalid pod, \
                         ts pod deleted={self.pod.metadata.deletion_timestamp} vs \
                         ts invalid_pod deleted={self.invalid_pod.metadata.deletion_timestamp} vs \
                         ts valid_pod created={self.valid_pod.metadata.creation_timestamp}, {msg}",
        )

        # Finally also delete the valid pod
        self.valid_pod.delete()
        self.status_data.update({"object": self.valid_pod})

        time.sleep(0.01)

        self.assertEqual(
            self.status_data.status_data[release].get("status"),
            "Deleted",
            "Release should be Deleted after delete of the last, valid pod.",
        )


class TestStatusConverter(unittest.TestCase):
    """Verifies the translation logic of k8s status objects to app status codes.

    This executes static method determine_status_from_k8s with signature:
    determine_status_from_k8s(status_object: V1PodStatus) -> Tuple[str, str, str]
    The response object has structure: status_object: phase, message, pod_message
    """

    def test_waiting_container_reason_pending(self):
        """
        This scenario tests a k8s pod status object with a container with the following status attributes:
        state=waiting, reason=PodInitializing
        """
        podstatus = PodStatus()
        podstatus.add_container_status("waiting", "PodInitializing")
        expected = ("PodInitializing", "", "")  # changed from Pending
        actual = StatusData.determine_status_from_k8s(podstatus)
        self.assertEqual(actual, expected)

    def test_running_container_status_not_ready(self):
        """
        This scenario tests a k8s pod status object with a container with the following status attributes:
        state=running, ready=false
        """
        podstatus = PodStatus()
        podstatus.add_container_status("running", None, ready=False)
        expected = ("Pending", "", "")
        actual = StatusData.determine_status_from_k8s(podstatus)
        self.assertEqual(actual, expected)

    def test_running_container_status_ready(self):
        """
        This scenario tests a k8s pod status object with a container with the following status attributes:
        state=running, ready=true
        """
        podstatus = PodStatus()
        podstatus.add_container_status("running", None, ready=True)
        expected = ("Running", "", "")
        actual = StatusData.determine_status_from_k8s(podstatus)
        self.assertEqual(actual, expected)

    def test_deleted_container(self):
        """
        This scenario tests a k8s pod status object with a container with the following status attributes:
        state=terminated, terminated reason="Terminated", message="Deleted", exit_code=1
        """
        podstatus = PodStatus()
        podstatus.add_container_status(
            "terminated", "Terminated", ready=False, exit_code=1
        )
        expected = ("Terminated", "", "")
        actual = StatusData.determine_status_from_k8s(podstatus)
        self.assertEqual(actual, expected)

    def test_terminated_init_container_reason_error(self):
        """
        This scenario tests a k8s pod status object with an init container with the following status attributes:
        Input: state=terminated, terminated.reason=PostStartHookError
        The Terminated exit code is set to 137 which could indicate for example that the container ran out of memory.
        See https://containersolutions.github.io/runbooks/posts/kubernetes/crashloopbackoff/#step-3
        """
        podstatus = PodStatus()
        podstatus.add_init_container_status(
            "terminated", "PostStartHookError", exit_code=137
        )
        expected = ("PostStartHookError", "", "")  # before: Pod Error
        actual = StatusData.determine_status_from_k8s(podstatus)
        self.assertEqual(actual, expected)

    def test_waiting_init_container_reason_error(self):
        """
        This scenario tests a k8s pod status object with an init container with the following status attributes:
        Input: state=waiting, terminated.reason=PostStartHookError
        """
        podstatus = PodStatus()
        podstatus.add_init_container_status("waiting", "PostStartHookError")
        expected = ("PostStartHookError", "", "")  # before: Pod Error
        actual = StatusData.determine_status_from_k8s(podstatus)
        self.assertEqual(actual, expected)

    def test_multiple_container_status_terminated_init_container_running_container(
        self,
    ):
        """
        This scenario tests a k8s pod status object with two containers.
        Init container input: state=terminated, terminated.reason=Completed
        Regular container input: stater=running, ready=false
        """
        podstatus = PodStatus()
        podstatus.add_init_container_status("terminated", "Completed")
        podstatus.add_container_status("running", "", ready=True)
        expected = ("Running", "", "")
        actual = StatusData.determine_status_from_k8s(podstatus)
        self.assertEqual(actual, expected)

    def test_missing_container_status(self):
        """This scenario tests an empty k8s pod status object."""
        podstatus = PodStatus()
        expected = (None, "", "")
        actual = StatusData.determine_status_from_k8s(podstatus)
        self.assertEqual(actual, expected)


class TestStatusDataUtilities(unittest.TestCase):
    """Verifies the app status utility methods."""

    def test_mapped_status(self):
        """Test the mapped status codes. Not all codes need to be tested."""
        actual = StatusData.get_mapped_status("CrashLoopBackOff")
        self.assertEqual(actual, "Error")

        actual = StatusData.get_mapped_status("Completed")
        self.assertEqual(actual, "Retrying...")

        actual = StatusData.get_mapped_status("ErrImagePull")
        self.assertEqual(actual, "Image Error")

    def test_mapped_status_nonexisting_code(self):
        """Test the mapped status codes in a scenario with a non-existing code."""
        actual = StatusData.get_mapped_status("NonexistingCode")
        self.assertEqual(actual, "NonexistingCode")


if __name__ == "__main__":
    unittest.main()
