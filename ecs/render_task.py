"""Render one Linux Fargate task; application metadata never supplies log options.

Uses only the Python standard library. This is deliberately a small lab renderer,
not an ECS service/network/IAM provisioning framework.
"""

import argparse
import json
import re
from pathlib import Path


HTTP_OPTIONS = {
    "Name": "http",
    "Port": "443",
    "tls": "on",
    "tls.verify": "on",
    "Format": "json_lines",
    "Json_date_key": "date",
    "Json_date_format": "iso8601",
}
APP_KEYS = {
    "application", "component", "image", "version", "cpu", "memory", "ports",
    "environment_variables",
}
RESERVED_ENV = {"SERVICE_NAME", "DEPLOYMENT_ENVIRONMENT", "APP_VERSION", "COMPONENT", "LOG_PROFILE"}
FARGATE_MEMORY = {
    256: {512, 1024, 2048},
    512: set(range(1024, 4097, 1024)),
    1024: set(range(2048, 8193, 1024)),
    2048: set(range(4096, 16385, 1024)),
    4096: set(range(8192, 30721, 1024)),
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def segment(value, label):
    require(isinstance(value, str) and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value)
            and len(value) <= 63, f"{label} must be a lowercase, hyphen-separated name (1-63 characters)")
    return value


def secret_arn(value):
    require(isinstance(value, str) and re.fullmatch(
        r"arn:aws[a-z-]*:secretsmanager:[a-z0-9-]+:\d{12}:secret:[A-Za-z0-9/_+=.@-]+", value),
        "Expected a full Secrets Manager ARN for a plain-text secret")
    return value


def cloudwatch_logs(platform, environment, prefix):
    return {"logDriver": "awslogs", "options": {
        "awslogs-group": environment["log_group"],
        "awslogs-region": platform["region"],
        "awslogs-stream-prefix": prefix,
        "mode": "non-blocking",
        "max-buffer-size": "5m",
    }}


def render(app, environment, platform, destinations, with_otel=False):
    require(isinstance(app, dict), "Application definition must be an object")
    require(not set(app) - APP_KEYS, "Unknown application keys; logging and routing belong to the platform")
    application = segment(app["application"], "application")
    component = segment(app["component"], "component")
    segment(environment, "environment")
    require(environment in platform["environments"] and environment in destinations,
            "Environment must have both platform settings and an approved HTTP Source mapping")
    settings = platform["environments"][environment]
    destination = destinations[environment]
    host = destination["host"]
    require(isinstance(host, str) and re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", host),
            "Destination host must be a hostname, without a scheme, path, port, or token")
    uri_arn = secret_arn(destination["uri_secret_arn"])
    require(app["image"] and isinstance(app["image"], str) and not re.search(r"\s", app["image"]),
            "image must be a nonempty container image reference")
    require(isinstance(app["version"], str) and bool(app["version"]), "version must be a nonempty string")
    require(type(app["cpu"]) is int and app["cpu"] > 0, "cpu must be a positive integer")
    require(type(app["memory"]) is int and app["memory"] > 0, "memory must be a positive integer")
    ports = app.get("ports", [])
    require(isinstance(ports, list) and all(type(p) is int and 1 <= p <= 65535 and p != 24224 for p in ports),
            "ports must be TCP port numbers; FireLens port 24224 must not be exposed")
    require(len(ports) == len(set(ports)), "ports must be unique")
    task_cpu, task_memory = int(platform["task_cpu"]), int(platform["task_memory"])
    require(task_memory in FARGATE_MEMORY.get(task_cpu, set()), "Unsupported Fargate CPU/memory pair for this lab")
    require(app["cpu"] <= task_cpu and app["memory"] + 128 + (256 if with_otel else 0) <= task_memory,
            "Task resources must fit the application, router, and optional OTel collector")
    env = app.get("environment_variables", {})
    require(isinstance(env, dict) and all(isinstance(k, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k)
            and isinstance(v, str) for k, v in env.items()), "environment_variables must map variable names to strings")
    require(not any(k in RESERVED_ENV or k.startswith("OTEL_") for k in env),
            "Application variables must not override shared identity or OTel settings")
    env = {**env, "SERVICE_NAME": application, "COMPONENT": component,
           "DEPLOYMENT_ENVIRONMENT": environment, "APP_VERSION": app["version"],
           "LOG_PROFILE": "json-v1", "OTEL_SERVICE_NAME": application,
           "OTEL_SDK_DISABLED": "false" if with_otel else "true", "OTEL_LOGS_EXPORTER": "none"}
    if with_otel:
        env["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4318"
    category = f"ecs/{environment}/{application}/{component}"
    container = {
        "name": f"{application}-{component}", "image": app["image"], "essential": True,
        "cpu": app["cpu"], "memory": app["memory"],
        "portMappings": [{"containerPort": p, "protocol": "tcp"} for p in ports],
        "environment": [{"name": k, "value": v} for k, v in sorted(env.items())],
        "dependsOn": [{"containerName": "log-router", "condition": "START"}],
        "logConfiguration": {
            "logDriver": "awsfirelens",
            "options": {**HTTP_OPTIONS, "Host": host, "Header": f"X-Sumo-Category {category}"},
            "secretOptions": [{"name": "URI", "valueFrom": uri_arn}],
        },
    }
    router = {
        "name": "log-router", "image": platform["router_image"], "essential": True,
        "memoryReservation": 128,
        "firelensConfiguration": {"type": "fluentbit", "options": {"enable-ecs-log-metadata": "true"}},
        "logConfiguration": cloudwatch_logs(platform, settings, "firelens"),
    }
    containers = [container, router]
    if with_otel:
        otel = settings["otel"]
        container["dependsOn"].append({"containerName": "otel-collector", "condition": "START"})
        containers.append({
            "name": "otel-collector", "image": otel["image"], "essential": True,
            "memoryReservation": 256,
            "secrets": [
                {"name": "SUMO_METRICS_ENDPOINT", "valueFrom": secret_arn(otel["metrics_secret_arn"])},
                {"name": "SUMO_TRACES_ENDPOINT", "valueFrom": secret_arn(otel["traces_secret_arn"])},
            ],
            "logConfiguration": cloudwatch_logs(platform, settings, "otel"),
        })
    task = {
        "family": f"{application}-{component}-{environment}",
        "executionRoleArn": settings["execution_role_arn"],
        "networkMode": "awsvpc", "requiresCompatibilities": ["FARGATE"],
        "runtimePlatform": {"cpuArchitecture": "X86_64", "operatingSystemFamily": "LINUX"},
        "cpu": str(task_cpu), "memory": str(task_memory), "containerDefinitions": containers,
    }
    if settings.get("task_role_arn"):
        task["taskRoleArn"] = settings["task_role_arn"]
    return task


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, type=Path)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--platform", required=True, type=Path)
    parser.add_argument("--destinations", required=True, type=Path)
    parser.add_argument("--with-otel", action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        # utf-8-sig also accepts JSON saved by Windows PowerShell with a BOM.
        app, platform, destinations = [json.loads(p.read_text(encoding="utf-8-sig"))
                                       for p in (args.app, args.platform, args.destinations)]
        task = render(app, args.environment, platform, destinations, args.with_otel)
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(2, f"Invalid deployment inputs: {error}\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(task, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
