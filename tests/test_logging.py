import copy
import io
import json
import logging
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from common.json_logging import JsonFormatter
from ecs.render_task import render


ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.platform = load("ecs/platform.example.json")
        self.destinations = load("ecs/destinations.example.json")
        self.app = load("ecs/examples/pokemon-catcher.json")

    def task(self, environment="sandbox", with_otel=False):
        return render(self.app, environment, self.platform, self.destinations, with_otel)

    def test_four_application_environment_combinations(self):
        implementations = []
        for app_name, component in [("pokemon-pinger", "worker"), ("pokemon-catcher", "api")]:
            self.app = load(f"ecs/examples/{app_name}.json")
            for environment in ("sandbox", "prod"):
                with self.subTest(application=app_name, environment=environment):
                    task = self.task(environment)
                    app, router = task["containerDefinitions"]
                    config = copy.deepcopy(app["logConfiguration"])
                    self.assertEqual(config["logDriver"], "awsfirelens")
                    self.assertEqual(config["options"].pop("Header"),
                                     f"X-Sumo-Category ecs/{environment}/{app_name}/{component}")
                    self.assertEqual(config["options"].pop("Host"), f"{environment}.example.invalid")
                    self.assertEqual(config.pop("secretOptions"), [{"name": "URI", "valueFrom":
                        f"arn:aws:secretsmanager:us-east-1:111122223333:secret:ecs/{environment}/sumo-uri-EXAMPL"}])
                    implementations.append(config)
                    self.assertNotIn("URI", config["options"])
                    self.assertNotIn("Match", config["options"])
                    self.assertEqual(config["options"]["tls.verify"], "on")
                    self.assertEqual(router["firelensConfiguration"], {
                        "type": "fluentbit", "options": {"enable-ecs-log-metadata": "true"}})
                    self.assertEqual(task["requiresCompatibilities"], ["FARGATE"])
                    self.assertEqual(task["networkMode"], "awsvpc")
                    self.assertNotIn("requiresAttributes", task)
                    self.assertNotIn("compatibilities", task)
                    self.assertEqual(task["executionRoleArn"],
                                     f"arn:aws:iam::111122223333:role/ecs-{environment}-execution")
                    values = {item["name"]: item["value"] for item in app["environment"]}
                    self.assertEqual(values["SERVICE_NAME"], app_name)
                    self.assertEqual(values["DEPLOYMENT_ENVIRONMENT"], environment)
                    self.assertEqual(values["OTEL_SDK_DISABLED"], "true")
                    self.assertEqual(json.loads(json.dumps(task)), task)
        self.assertTrue(all(config == implementations[0] for config in implementations))

    def test_otel_is_optional_and_uses_secrets(self):
        for environment in ("sandbox", "prod"):
            task = self.task(environment, with_otel=True)
            app, _, collector = task["containerDefinitions"]
            values = {item["name"]: item["value"] for item in app["environment"]}
            self.assertEqual(values["OTEL_SDK_DISABLED"], "false")
            self.assertEqual(values["OTEL_LOGS_EXPORTER"], "none")
            self.assertEqual(values["OTEL_EXPORTER_OTLP_ENDPOINT"], "http://localhost:4318")
            self.assertEqual(len(collector["secrets"]), 2)
            self.assertTrue(all(f"ecs/{environment}/otel-" in s["valueFrom"] for s in collector["secrets"]))

    def test_rejects_invalid_category_segments(self):
        for value in ("", "Prod", "my/app", "my app", "worker\nHeader injected", "a" * 64):
            for key in ("application", "component"):
                with self.subTest(key=key, value=value):
                    app = {**self.app, key: value}
                    with self.assertRaises(ValueError):
                        render(app, "sandbox", self.platform, self.destinations)

    def test_rejects_unmapped_environment(self):
        with self.assertRaises(ValueError):
            self.task("staging")
        del self.destinations["prod"]
        with self.assertRaises(ValueError):
            self.task("prod")

    def test_rejects_application_logging_overrides(self):
        for key in ("source_category", "logConfiguration", "host", "environment"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                render({**self.app, key: "override"}, "sandbox", self.platform, self.destinations)
        for name in ("DEPLOYMENT_ENVIRONMENT", "SERVICE_NAME", "OTEL_LOGS_EXPORTER"):
            self.app["environment_variables"] = {name: "override"}
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.task()

    def test_rejects_urls_or_plain_tokens_in_destination(self):
        self.destinations["sandbox"]["host"] = "https://example.invalid/receiver/v1/http/EXAMPLE"
        with self.assertRaises(ValueError):
            self.task()
        self.destinations["sandbox"]["host"] = "sandbox.example.invalid"
        self.destinations["sandbox"]["uri_secret_arn"] = "/receiver/v1/http/EXAMPLE"
        with self.assertRaises(ValueError):
            self.task()

    def test_rejects_bad_ports_and_resources(self):
        for ports in ([24224], [8080, 8080], [-1], [True], [70000]):
            with self.subTest(ports=ports), self.assertRaises(ValueError):
                render({**self.app, "ports": ports}, "sandbox", self.platform, self.destinations)
        for key, value in (("cpu", 2048), ("memory", 2048), ("memory", -1), ("cpu", "256")):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                render({**self.app, key: value}, "sandbox", self.platform, self.destinations)
        self.platform["task_memory"] = "512"
        with self.assertRaises(ValueError):
            self.task()


class PayloadTests(unittest.TestCase):
    def test_profile_has_identity_business_fields_and_single_line_exceptions(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        logger = logging.getLogger("profile-test")
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.INFO)
        with patch.dict(os.environ, {"SERVICE_NAME": "pokemon-catcher", "DEPLOYMENT_ENVIRONMENT": "sandbox",
                                    "APP_VERSION": "1.0.0", "COMPONENT": "api"}):
            try:
                raise ValueError("a multiline\nexception")
            except ValueError:
                logger.exception("Catch failed", extra={"fields": {"pokemon_id": 25, "service": "spoofed"}})
        self.assertEqual(len(stream.getvalue().splitlines()), 1)
        event = json.loads(stream.getvalue())
        self.assertEqual(event["service"], "pokemon-catcher")
        self.assertEqual(event["pokemon_id"], 25)
        self.assertEqual(event["log_profile"], "json-v1")
        self.assertEqual(event["level"], "ERROR")
        self.assertIn("exception", event)
        for key in ("timestamp", "message", "version", "environment", "component"):
            self.assertIn(key, event)


if __name__ == "__main__":
    unittest.main()
