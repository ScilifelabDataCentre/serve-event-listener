import datetime
import unittest
from unittest.mock import MagicMock

from tests.create_mock_data import pod_basic_delete, pod_basic_startup


class TestPodProcessing(unittest.TestCase):
    release = "some_release"

    def test_pod_startup(self):
        status_data = pod_basic_startup(self.release)

        expected_status = "running"

        self.assertEqual(status_data.get(self.release).get("status"), expected_status)

    def test_pod_delete(self):
        status_data = pod_basic_delete(self.release)

        expected_status = "Deleted"

        self.assertEqual(status_data.get(self.release).get("status"), expected_status)

    def test_pod_deletion(self):
        pass
        # Test scenario for pod deletion
        # Create mock events for deleting a pod and verify the status_data

    def test_failed_image_pull(self):
        pass
        # Test scenario for a deployment initializing a pod with a failed image pull
        # Create mock events for the failed image pull and verify the status_data


if __name__ == "__main__":
    unittest.main()
