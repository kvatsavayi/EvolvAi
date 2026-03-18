FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV APP_DATA_DIR=/app/data
ENV APP_CONFIG_DIR=/app/config
ENV DATABASE_URL=sqlite:////app/data/db.sqlite
ENV ARTIFACT_DIR=/app/data/artifacts

COPY . /app

RUN pip install --no-cache-dir .
RUN mkdir -p /app/data /app/config /app/config/personas && cp -r /app/personas/. /app/config/personas/

CMD ["python", "-c", "from deployment.abacus_workflow_entry import run_abacus_workflow; print('abacus runtime ready')"]
