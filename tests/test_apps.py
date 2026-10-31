"""Application smoke tests: no network, ECS, or Sumo account required."""

import contextlib
import io
import json
import logging
import os
from pathlib import Path
import runpy
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]


class AppTests(unittest.TestCase):
    def setUp(self):
        self.handlers = logging.getLogger().handlers[:]
        self.level = logging.getLogger().level

    def tearDown(self):
        logging.getLogger().handlers = self.handlers
        logging.getLogger().setLevel(self.level)

    def test_catcher_response_and_log(self):
        capture = io.StringIO()
        with patch.dict(os.environ, {"OTEL_SDK_DISABLED": "true", "SERVICE_NAME": "pokemon-catcher",
                                     "DEPLOYMENT_ENVIRONMENT": "sandbox"}), contextlib.redirect_stdout(capture):
            module = runpy.run_path(str(ROOT / "app2/main.py"))
            with patch("random.random", return_value=0.1):
                response = module["app"].test_client().get("/catch/25")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"pokemon_id": 25, "caught": True})
        event = json.loads(capture.getvalue().splitlines()[-1])
        self.assertEqual(event["service"], "pokemon-catcher")
        self.assertEqual(event["pokemon_id"], 25)
        self.assertTrue(event["caught"])

    def test_pinger_fetch_and_error_are_json_without_otel_exporters(self):
        import requests

        class EndIteration(BaseException):
            pass

        for status in (200, 503):
            capture = io.StringIO()
            response = Mock(status_code=status)
            response.json.return_value = {"name": "pikachu"}
            with self.subTest(status=status), patch.dict(os.environ, {
                "OTEL_SDK_DISABLED": "true", "SERVICE_NAME": "pokemon-pinger", "DEPLOYMENT_ENVIRONMENT": "prod"
            }), contextlib.redirect_stdout(capture), patch.object(requests.Session, "get", return_value=response), \
                    patch("time.sleep", side_effect=EndIteration), patch("random.randint", return_value=25), \
                    patch("opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter") as exporter:
                with self.assertRaises(EndIteration):
                    runpy.run_path(str(ROOT / "app/main.py"))
                exporter.assert_not_called()
            events = [json.loads(line) for line in capture.getvalue().splitlines()]
            event = events[-1]
            self.assertEqual(event["service"], "pokemon-pinger")
            self.assertEqual(event["status"], status)
            self.assertEqual(event["pokemon_id"], 25)
            self.assertEqual(event["level"], "INFO" if status == 200 else "WARNING")


if __name__ == "__main__":
    unittest.main()
