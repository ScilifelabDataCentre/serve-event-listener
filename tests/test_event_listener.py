import datetime
import unittest
from unittest.mock import MagicMock

def create_mock_pod_event(namespace, release, status, creation_timestamp, deletion_timestamp=None):
    """
    Create a mock pod event for testing.

    Parameters:
    - namespace (str): The namespace of the pod.
    - release (str): The release label of the pod.
    - status (str): The status of the pod.
    - creation_timestamp (datetime): The creation timestamp of the pod.
    - deletion_timestamp (datetime, optional): The deletion timestamp of the pod.

    Returns:
    - dict: Mock pod event.
    """
    mock_pod = {
        "object": {
            "metadata": {
                "namespace": namespace,
                "labels": {"release": release},
                "creation_timestamp": creation_timestamp.isoformat(),
                "deletion_timestamp": deletion_timestamp.isoformat() if deletion_timestamp else None,
            },
        },
    }
    # Add other relevant fields to the mock_pod if needed
    return mock_pod

class TestPodProcessing(unittest.TestCase):
    def test_initializing_pod(self):
        namespace = "test-namespace"
        release = "test-release"
        creation_timestamp = datetime.datetime(2024, 1, 1, 0, 0, 0)
        
        mock_data = [
            create_mock_pod_event(namespace, release, "Running", creation_timestamp),
            # Add other events if needed
        ]

        k8s_watch_instance = MagicMock()
        k8s_watch_instance.stream.return_value = mock_data

        k8s_api_instance = MagicMock()

        status_data = {} #TODO: fix this #start_stream(k8s_watch_instance, k8s_api_instance, namespace, "release=test-release")

        expected_status_data = {
            "test-release": {
                "creation_timestamp": creation_timestamp,
                "deletion_timestamp": None,
                "status": "Running",
            }
        }

        self.assertEqual(status_data, expected_status_data)

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