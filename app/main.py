import requests
import random
import time
import os
import json
import logging # Use standard logging

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
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, DEPLOYMENT_ENVIRONMENT
# For auto-instrumenting HTTP requests
from opentelemetry.instrumentation.requests import RequestsInstrumentor
# For setting span status
from opentelemetry.trace import SpanKind, Status, StatusCode
# --- End OpenTelemetry Imports ---

# Configure standard logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- OpenTelemetry Setup ---
# Read configuration from environment variables set in task definition
service_name = os.getenv("OTEL_SERVICE_NAME", "pokemon-pinger-default")
deployment_environment = os.getenv("DEPLOYMENT_ENVIRONMENT", "unknown") # Example standard attribute
otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318") # Default if not set

# Define resource attributes for this service
resource_attributes = {
    SERVICE_NAME: service_name,
    DEPLOYMENT_ENVIRONMENT: deployment_environment,
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
tracer_provider = TracerProvider(resource=resource)
# Configure the OTLP/HTTP exporter using the endpoint from env var
# The OTEL_TRACES_EXPORTER=otlp_proto_http env var ensures this is used
span_exporter = OTLPSpanExporter(endpoint=f"{otlp_endpoint}/v1/traces")
processor = BatchSpanProcessor(span_exporter)
tracer_provider.add_span_processor(processor)
trace.set_tracer_provider(tracer_provider)
tracer = trace.get_tracer(__name__)
RequestsInstrumentor().instrument()
logger.info(f"OTel Tracing initialized for service: {service_name}, exporting to {otlp_endpoint}/v1/traces")
# --- End Tracing Setup ---

# --- Metrics Setup ---
# Configure the OTLP/HTTP exporter using the endpoint from env var
# The OTEL_METRICS_EXPORTER=otlp_proto_http env var ensures this is used
metric_exporter = OTLPMetricExporter(endpoint=f"{otlp_endpoint}/v1/metrics") # Corrected: Singular Metric
reader = PeriodicExportingMetricReader(metric_exporter)
meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
metrics.set_meter_provider(meter_provider)
meter = metrics.get_meter(__name__)

# Define custom metrics
pokemon_fetch_counter = meter.create_counter(
    name="pokemon.fetch.count",
    description="Counts the number of pokemon fetch attempts",
    unit="1"
)
pokemon_fetch_success_counter = meter.create_counter(
    name="pokemon.fetch.success",
    description="Counts successful pokemon fetches",
    unit="1"
)
pokemon_fetch_error_counter = meter.create_counter(
    name="pokemon.fetch.error",
    description="Counts failed pokemon fetches (HTTP errors or exceptions)",
    unit="1"
)
logger.info(f"OTel Metrics initialized for service: {service_name}, exporting to {otlp_endpoint}/v1/metrics")
# --- End Metrics Setup ---

# --- Original Application Logic ---
INTERVAL_SEC = int(os.getenv("INTERVAL_SEC", 30))
session = requests.Session()

logger.info(f"Starting Pokemon Pinger main loop (Interval: {INTERVAL_SEC}s)...")

while True:
    with tracer.start_as_current_span("fetch_pokemon_loop") as loop_span:
        pokemon_id = 0
        try:
            pokemon_id = random.randint(1, 300)
            loop_span.set_attribute("pokemon.id.attempted", pokemon_id)
            pokemon_fetch_counter.add(1, {"pokemon.id": str(pokemon_id)})

            logger.info(json.dumps({
                "message": f"fetching number {pokemon_id}"
            })) 

            url = f"https://pokeapi.co/api/v2/pokemon/{pokemon_id}"
            response = session.get(url, timeout=5)
            status = response.status_code
            loop_span.set_attribute("http.status_code", status)

            if status == 200:
                data = response.json()
                pokemon_name = data.get("name", "unknown")
                loop_span.set_attribute("pokemon.name", pokemon_name)
                loop_span.set_status(Status(StatusCode.OK))
                pokemon_fetch_success_counter.add(1, {"pokemon.id": str(pokemon_id), "pokemon.name": pokemon_name})

                logger.info(json.dumps({
                    "pokemon_id": pokemon_id,
                    "pokemon_name": pokemon_name,
                    "status": 200
                })) 
            else:
                error_message = f"HTTP {status}"
                loop_span.set_attribute("error.message", error_message)
                loop_span.set_status(Status(StatusCode.ERROR, f"PokeAPI request failed with status {status}"))
                pokemon_fetch_error_counter.add(1, {"pokemon.id": str(pokemon_id), "http.status_code": str(status)})

                logger.warning(json.dumps({
                    "pokemon_id": pokemon_id,
                    "error": error_message,
                    "status": status
                })) 

        except Exception as e:
            logger.error(f"Exception during fetch for ID {pokemon_id}: {e}", exc_info=True)
            loop_span.record_exception(e)
            loop_span.set_status(Status(StatusCode.ERROR, f"Exception: {str(e)}"))
            pokemon_fetch_error_counter.add(1, {"pokemon.id": str(pokemon_id), "exception.type": type(e).__name__})

            print(json.dumps({
                "pokemon_id": pokemon_id,
                "error": str(e)
            }), flush=True) 

        finally:
            time.sleep(INTERVAL_SEC)
