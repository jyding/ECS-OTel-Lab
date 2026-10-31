import random
import time
import os
from threading import Thread
import logging
from common.json_logging import configure_logging

from flask import Flask, jsonify

# --- OpenTelemetry Imports ---
from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
# Use the OTLP/HTTP exporter as configured in the collector setup
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter # Corrected: Singular Metric
# For setting service name and other attributes
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
# For auto-instrumenting Flask applications
from opentelemetry.instrumentation.flask import FlaskInstrumentor
# --- End OpenTelemetry Imports ---

# Configure standard logging
configure_logging()
logger = logging.getLogger(__name__)

# --- OpenTelemetry Setup ---
# Read configuration from environment variables set in task definition
service_name = os.getenv("OTEL_SERVICE_NAME", "pokemon-catcher-default")
deployment_environment = os.getenv("DEPLOYMENT_ENVIRONMENT", "unknown") # Example standard attribute
otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318") # Default if not set

# Define resource attributes for this service
resource_attributes = {
    SERVICE_NAME: service_name,
    "deployment.environment.name": deployment_environment,
}
resource_env_vars = os.getenv("OTEL_RESOURCE_ATTRIBUTES", "")
if resource_env_vars:
    try:
        for pair in resource_env_vars.split(','):
            key, value = pair.split('=', 1)
            resource_attributes[key.strip()] = value.strip()
            # logger.info(f"Added resource attribute: {key.strip()}={value.strip()}") # Log only if needed
    except Exception as e:
        logger.warning(f"Could not parse OTEL_RESOURCE_ATTRIBUTES='{resource_env_vars}': {e}")

resource = Resource(attributes=resource_attributes)

# --- Tracing Setup ---
otel_enabled = os.getenv("OTEL_SDK_DISABLED", "true").lower() != "true"
if otel_enabled:
    tracer_provider = TracerProvider(resource=resource)
    span_exporter = OTLPSpanExporter(endpoint=f"{otlp_endpoint}/v1/traces")
    processor = BatchSpanProcessor(span_exporter)
    tracer_provider.add_span_processor(processor)
    trace.set_tracer_provider(tracer_provider)
    logger.info(f"OTel Tracing initialized for service: {service_name}, exporting to {otlp_endpoint}/v1/traces")
    # --- End Tracing Setup ---

    # --- Metrics Setup ---
    metric_exporter = OTLPMetricExporter(endpoint=f"{otlp_endpoint}/v1/metrics") # Corrected: Singular Metric
    reader = PeriodicExportingMetricReader(metric_exporter)
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(meter_provider)


tracer = trace.get_tracer(__name__)
meter = metrics.get_meter(__name__)

# Define custom metrics
pokemon_catch_attempt_counter = meter.create_counter(
    name="pokemon.catch.attempts",
    description="Counts catch attempts",
    unit="1"
)
pokemon_catch_success_counter = meter.create_counter(
    name="pokemon.catch.success",
    description="Counts successful catches",
    unit="1"
)
pokemon_catch_failure_counter = meter.create_counter(
    name="pokemon.catch.failures",
    description="Counts failed catches",
    unit="1"
)
if otel_enabled:
    logger.info(f"OTel Metrics initialized for service: {service_name}")
# --- End Metrics Setup ---


# --- Original Application Logic ---
INTERVAL_SEC = int(os.getenv("INTERVAL_SEC", "10"))

app = Flask(__name__)

# --- Apply OTel Flask Instrumentation ---
if otel_enabled:
    FlaskInstrumentor().instrument_app(app)
    logger.info("FlaskInstrumentor applied.")
# --- End OTel Flask Instrumentation ---

@app.route("/catch/<int:pkmn_id>")
def catch(pkmn_id):
    current_span = trace.get_current_span()
    current_span.set_attribute("pokemon.id.caught", pkmn_id)

    caught = random.random() < 0.5
    log = {
        "pokemon_id": pkmn_id,
        "caught": caught
    }
    current_span.set_attribute("pokemon.caught_result", caught)

    pokemon_catch_attempt_counter.add(1, {"pokemon.id": str(pkmn_id)})
    if caught:
        pokemon_catch_success_counter.add(1, {"pokemon.id": str(pkmn_id)})
    else:
        pokemon_catch_failure_counter.add(1, {"pokemon.id": str(pkmn_id)})

    # Log to stdout (for FireLens) using standard logger
    logger.info("Pokemon catch attempted", extra={"fields": log})
    return jsonify(log)

def heartbeat():
    while True:
        logger.info("Heartbeat", extra={"fields": {"heartbeat": time.time()}})
        time.sleep(INTERVAL_SEC)

if __name__ == "__main__":
    logger.info(f"Starting Pokemon Catcher Flask app (Heartbeat: {INTERVAL_SEC}s)...")
    Thread(target=heartbeat, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)

