import os
import unittest

import requests

from tests.integration.base import IntegrationTestCase


class TestTargetAPI(IntegrationTestCase):
    """Basic live API checks."""

    def setUp(self):
        base = os.getenv("BASE_URL")
        if not base:
            raise unittest.SkipTest("BASE_URL not set")
        self.base = base

    def test_are_you_there(self):
        r = requests.get(f"{self.base}/openapi/v1/are-you-there", timeout=5)
        self.assertEqual(r.status_code, 200)
