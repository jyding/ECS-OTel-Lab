FROM python:3.12-slim

WORKDIR /app
COPY app2/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app2/main.py .
COPY common/ ./common/
ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
