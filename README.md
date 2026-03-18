# agent-pods

Prototype pod-based agent runtime with lineage tracking and executor reward firewall.

## Quickstart

```bash
cd agent-pods
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
pytest -q
uvicorn apps.api.main:app --reload --port 8000
```

## API

- `POST /v1/requests`
- `GET /v1/requests/{id}`
- `GET /v1/pods`
- `POST /v1/replay/{run_id}`
