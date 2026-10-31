FROM python:3.12-slim

WORKDIR /app
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/main.py .
COPY common/ ./common/
ENV PYTHONUNBUFFERED=1

# main.py already configures tracing/metrics; avoid a second auto-instrumentation layer.
CMD ["python", "main.py"]
