FROM python:3.11-slim AS base
RUN groupadd -r appuser && useradd -r -g appuser appuser
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ src/
ENV PYTHONPATH=/app
ARG SERVICE=api
ENV SERVICE=${SERVICE}
EXPOSE 8000
USER appuser
CMD ["sh", "-c", "python -m uvicorn src.${SERVICE_MODULE}:app --host 0.0.0.0 --port 8000"]
