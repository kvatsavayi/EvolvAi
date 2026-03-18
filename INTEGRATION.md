# Abacus.AI LLM Integration

## Overview

EvolvAi now supports **Abacus.AI** as a first-class LLM backend provider alongside the existing Mock, Ollama, llama.cpp, and Remote (OpenAI-compatible) backends.

The integration uses the **Abacus.AI Python SDK** (`abacusai` package) and the `evaluate_prompt` API, which provides access to a wide range of foundation models.

## Architecture

### Backend Selection Flow

```
Environment / .env
    └── MODEL_PROVIDER=abacusai
           └── ExecutorRunner._build_backend()
                  └── AbacusAIBackend()
                         └── abacusai.ApiClient().evaluate_prompt(...)
```

### Key Files Modified

| File | Change |
|------|--------|
| `core/executor/runner.py` | Added `AbacusAIBackend` class; updated `_build_backend()` to recognize `abacusai` provider |
| `apps/api/main.py` | Added `.env` auto-loading via `python-dotenv` |
| `.env` | Updated `MODEL_PROVIDER=abacusai`, `ABACUS_MODEL`, `MODEL_MAX_TOKENS=1024` |

### AbacusAIBackend Class

- **Location:** `core/executor/runner.py`
- **Protocol:** Implements `ExecutorBackend` (same interface as Mock, Ollama, etc.)
- **Methods:**
  - `rail(snapshot)` — Returns provider metadata (model name, base URL)
  - `generate(run_id, dna, snapshot)` — Calls `evaluate_prompt` and returns the standard executor output dict

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MODEL_PROVIDER` | `mock` | Set to `abacusai` to use Abacus.AI |
| `ABACUS_MODEL` | `CLAUDE_V3_5_SONNET` | Model identifier (see available models below) |
| `MODEL_NAME` | (fallback) | Alternative to `ABACUS_MODEL` |
| `MODEL_TEMPERATURE` | `0.2` | Sampling temperature |
| `MODEL_TOP_P` | `1.0` | Top-p sampling parameter |
| `MODEL_MAX_TOKENS` | `1024` | Maximum output tokens |
| `ABACUS_API_KEY` | (pre-configured) | API key — already set in the DeepAgent environment |

### Available Models

The Abacus.AI SDK supports many models. Common choices:

| Model Name | Description |
|------------|-------------|
| `CLAUDE_V3_5_SONNET` | Claude 3.5 Sonnet (default) |
| `CLAUDE_V3_5_HAIKU` | Claude 3.5 Haiku (faster, cheaper) |
| `CLAUDE_V3_7_SONNET` | Claude 3.7 Sonnet |
| `CLAUDE_V4_5_SONNET` | Claude 4.5 Sonnet (latest) |
| `CLAUDE_V4_5_OPUS` | Claude 4.5 Opus (most capable) |
| `GPT4O` | GPT-4o |
| `GEMINI_2_0_FLASH` | Gemini 2.0 Flash |

### Per-Persona Configuration

Environment variables support persona-scoped overrides:
- `ABACUS_MODEL_RESEARCH=CLAUDE_V3_5_HAIKU` — use Haiku for research persona
- `MODEL_TEMPERATURE_IMPLEMENTATION=0.1` — lower temperature for code generation

## Switching Between Backends

```bash
# Use Abacus.AI (real LLM)
export MODEL_PROVIDER=abacusai

# Use Mock (for testing, no API calls)
export MODEL_PROVIDER=mock

# Use Ollama (local models)
export MODEL_PROVIDER=ollama
```

## Testing

```bash
# Run all tests with mock backend (no API calls)
EXECUTOR_BACKEND=mock python3 -m pytest tests/ -q

# Verify Abacus.AI integration manually
MODEL_PROVIDER=abacusai python3 -c "
from core.executor.runner import ExecutorRunner
import os; os.environ['MODEL_PROVIDER'] = 'abacusai'
runner = ExecutorRunner()
print(type(runner.backend).__name__)  # AbacusAIBackend
"
```

## Running the Application

```bash
cd /home/ubuntu/evolvai

# Start the backend (serves both API and frontend)
python3 -m uvicorn apps.api.main:app --host 0.0.0.0 --port 8000

# Access points:
# - API: http://localhost:8000/v1/
# - Health: http://localhost:8000/health
# - UI: http://localhost:8000/
```

### VM Preview URL

When running in the DeepAgent VM, the application is accessible at:
- **https://12bd60444e.na101.preview.abacusai.app** (maps to port 3000)
- **https://12bd60444e-8000.na101.preview.abacusai.app** (maps to port 8000)

## Deployment Notes

- The `ABACUS_API_KEY` environment variable is pre-configured in the DeepAgent environment
- The VM preview URL is tied to the VM lifecycle and will stop working when the VM becomes inactive
- For production deployment, the application would need to be deployed on your own infrastructure with the Abacus.AI API key configured
