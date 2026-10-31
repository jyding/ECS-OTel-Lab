"""The lab's json-v1 application log profile (one JSON object per line)."""

import json
import logging
import os
import sys
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record):
        event = dict(getattr(record, "fields", {}))
        # Identity and schema keys cannot be replaced by business-specific fields.
        event.update({
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "service": os.getenv("SERVICE_NAME", os.getenv("OTEL_SERVICE_NAME", "unknown")),
            "environment": os.getenv("DEPLOYMENT_ENVIRONMENT", "local"),
            "component": os.getenv("COMPONENT", "unknown"),
            "version": os.getenv("APP_VERSION", "1.0.0"),
            "log_profile": "json-v1",
        })
        if record.exc_info:
            event["exception"] = self.formatException(record.exc_info)
        return json.dumps(event, ensure_ascii=False, default=str)


def configure_logging():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
