# Logging standard and optional OTel details

## Two layers of JSON

The application writes one UTF-8 JSON object per line. The shared
[`common/json_logging.py`](../common/json_logging.py) formatter handles ordinary messages,
business fields, and exceptions. Stack traces are escaped inside a JSON string instead
of producing separate unstructured lines. Example application event:

```json
{
  "timestamp": "2026-10-31T12:00:00+00:00",
  "level": "INFO",
  "message": "Pokemon catch attempted",
  "service": "pokemon-catcher",
  "environment": "sandbox",
  "component": "api",
  "version": "1.0.0",
  "log_profile": "json-v1",
  "pokemon_id": 25,
  "caught": true
}
```

FireLens receives stdout/stderr and adds its envelope. The HTTP output serializes the
envelope as JSON lines. A representative event (illustrative, not captured live) is:

```json
{
  "date": "2026-10-31T12:00:00.000000Z",
  "source": "stdout",
  "container_id": "example-container-id",
  "container_name": "pokemon-catcher-api",
  "ecs_cluster": "lab-sandbox",
  "ecs_task_arn": "arn:aws:ecs:us-east-1:111122223333:task/lab-sandbox/example",
  "ecs_task_definition": "pokemon-catcher-api-sandbox:7",
  "log": "{\"timestamp\":\"2026-10-31T12:00:00+00:00\",\"level\":\"INFO\",\"message\":\"Pokemon catch attempted\",\"service\":\"pokemon-catcher\",\"environment\":\"sandbox\",\"component\":\"api\",\"version\":\"1.0.0\",\"log_profile\":\"json-v1\",\"pokemon_id\":25,\"caught\":true}"
}
```

The outer `date` is the Fluent Bit event time. The inner `timestamp` is application time.
`ecs_task_definition` includes the revision. Fargate has no EC2 instance ID to add.
The category is HTTP metadata, not another application JSON key.

Keeping the JSON payload under `log` preserves the ECS envelope and avoids field-name
collisions. The lab intentionally has no custom parsing filter. To extract application
fields in Sumo, parse the envelope and then its `log` string:

```text
_sourceCategory=ecs/*/pokemon-catcher/*
| json "log" as app_log
| json field=app_log "level", "message", "service", "environment", "pokemon_id" nodrop
```

Find a task after selecting a category:

```text
_sourceCategory=ecs/sandbox/pokemon-catcher/api
| json "ecs_task_arn", "ecs_task_definition", "container_name" nodrop
| where ecs_task_definition = "pokemon-catcher-api-sandbox:7"
| count by ecs_task_arn, container_name
```

Confirm actual metadata values in your own deployment before filtering on them. Search
syntax uses Sumo's [JSON operator](https://www.sumologic.com/help/docs/search/search-query-language/parse-operators/parse-json-formatted-logs/).

## A small number of log profiles

`json-v1` is the profile implemented by the two applications. Platform teams can approve
a transitional `legacy-text` profile for applications that cannot yet emit JSON. The
shared HTTP transport can carry text in the same envelope's `log` field, but text needs
different search/FER parsing. The renderer does not currently implement profile selection
or arbitrary custom filters. Add an explicit, reviewed profile only when needed, keeping
the transport and destination policy shared. Framework startup banners may also be text;
use `nodrop` where appropriate and inspect unmatched records.

Scope common Field Extraction Rules to `ecs/*` for envelope fields, and to appropriate
application/profile populations for payload fields. Do not assume every message is
structured JSON just because the HTTP output uses `json_lines`. Avoid broad FER rules
that silently interpret legacy text as `json-v1`.

## Who enforces what

| Owner/control | Responsibility |
| --- | --- |
| Application team | Identity, image/resources, structured payload and business fields |
| Platform-owned/versioned renderer | FireLens output, TLS, ECS metadata and category construction |
| Platform IaC | Stable Collectors/Sources, destination mapping and secret lifecycle |
| CI/CD and review | Required metadata, approved environment mappings, renderer version and image/config changes |
| AWS IAM | Which execution roles may retrieve each environment's ingestion secret |
| Sumo RBAC | Who can view/manage Collectors and Sources; appropriate search access |

Developers should generally consume approved ingestion boundaries and naming rules rather
than create arbitrary Collectors or category conventions. In a larger organization,
publish/version the shared code, require its use in delivery pipelines, and protect
platform-owned mapping changes in review.

Sumo's `Manage Collectors` capability controls management of Collectors and Sources;
`View Collectors` permits viewing them. Restrict unnecessary management capabilities.
RBAC does **not** validate application metadata or enforce this category grammar on
incoming HTTP headers. A holder of an ingestion endpoint can supply a different category.
Use CI/CD, controlled secrets, IAM and IaC for those controls; a category is not a security
boundary. See [Sumo role capabilities](https://www.sumologic.com/help/docs/manage/users-roles/roles/role-capabilities/).

## Optional OTel setup

The apps retain their original custom metrics and request/Flask tracing. OTel SDK
initialization is explicitly gated by `OTEL_SDK_DISABLED`, which defaults to `true` in
this lab. The Dockerfiles run `python main.py` because the apps already configure the
SDK; layering `opentelemetry-instrument` on top could initialize telemetry twice.

For ECS:

1. Build `docker build -t otel-with-config:lab collector` and push the image to your registry.
2. Use a Sumo OTLP/HTTP Source managed by your platform. It is separate from the HTTP
   Logs Sources created by this example Terraform. Store the **full metrics and traces
   endpoint URLs** as two plain-text Secrets Manager secrets. Do not reuse the log URI
   secret; the Collector needs full signal-specific URLs.
3. Set the environment's `otel.image`, `otel.metrics_secret_arn`, and
   `otel.traces_secret_arn` in the local platform file. Grant its execution role read
   access to those secrets too.
4. Add `--with-otel` to the normal render command. This adds a Collector sidecar, secrets
   injection, startup dependency, and `http://localhost:4318` for the app's OTLP/HTTP
   exporters. Containers in this Fargate task share the network namespace.

The Collector reads `${env:SUMO_METRICS_ENDPOINT}` and `${env:SUMO_TRACES_ENDPOINT}` and
exports only metrics/traces. It logs its own diagnostics to CloudWatch. It does not
consume application logs or determine their Source Categories.

For local Compose, supply the two endpoint variables privately in your shell and run:

```bash
OTEL_SDK_DISABLED=false docker compose --profile otel up --build
```

Without real endpoints the profile's `example.invalid` defaults cannot export data.
Do not commit an `.env` file with credentials. Stop with
`docker compose --profile otel down`. No application ports other than the catcher's
localhost-bound 8080 are published.

See [Sumo OTLP/HTTP Sources](https://www.sumologic.com/help/docs/send-data/hosted-collectors/http-source/otlp/)
and [Collector environment-variable configuration](https://opentelemetry.io/docs/collector/configuration/).
