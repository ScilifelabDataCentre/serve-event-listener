import datetime
import unittest
from unittest.mock import MagicMock

from create_mock_data import pod_start_event_generator


class TestPodProcessing(unittest.TestCase):
    def test_initializing_pod(self):
        status_data = {}
        # for event in pod_start_event_generator():
        #    print(event)
        #    status_data = update_status_data(event, status_data)
        #    print(status_data)

        expected_status = "running"

        self.assertEqual(status_data.get("status"), expected_status)

    def test_scaling_pods(self):
        pass
        # Test scenario for scaling up and down pods in a replicaset
        # Similar to the first test, you can create mock events for scaling pods

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
