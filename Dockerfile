FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY alembic ./alembic
COPY src ./src
COPY artifacts ./artifacts
COPY docs/audit ./docs/audit
RUN pip install --no-cache-dir ".[postgres]"
USER 65534
EXPOSE 8000
CMD ["uvicorn", "sensorguard.api:app", "--host", "0.0.0.0", "--port", "8000"]
