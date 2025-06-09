FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
# add the distro here
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir opentelemetry-distro

COPY main.py .

# optional: auto‑instrument everything when the container starts
CMD ["opentelemetry-instrument", "python", "main.py"]
