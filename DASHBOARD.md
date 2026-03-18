# EvolvAI QA Dashboard

A modern, interactive dashboard for managing and monitoring LLM quality assurance testing with the EvolvAI three-layer hybrid evaluation system.

## Features

### 📊 Overview
- System health status (online/offline indicator)
- Quick stats: models available, pass rates, attractors found, total tests
- Model pass rate bar chart and test category donut chart
- Recent test runs feed
- Quick start guide with step-by-step workflow

### ⚙️ Test Configuration
- **Model Selection**: Choose from 7+ LLM models (Claude, GPT-4o, Gemini, Llama, etc.)
- **Test Suite Selection**: Capability, Safety, Adversarial, Regression categories
- **Parameters**: Tests per category, difficulty level, consistency runs
- **Advanced Options**: Toggle normalization, LLM-as-Judge, custom test prompts
- Save/load configurations to localStorage

### ▶️ Run Tests
- One-click test execution (single model or multi-model comparison)
- Real-time progress bar with phase indicators
- Live results streaming with pass/fail indicators
- Cancel functionality
- Automatic history recording

### 📈 Results Dashboard
- Summary cards (total tests, passed, failed, avg latency)
- **Category Breakdown**: Stacked bar chart (passed/failed per category)
- **Performance Radar**: Radar chart showing pass rate by category
- **Pass/Fail Distribution**: Donut chart
- **Latency Analysis**: Bar chart by category
- **Model Comparison**: Side-by-side when comparing multiple models
- Expandable detailed test cases with scores and failure details
- Filter by category and pass/fail status
- Export results as JSON

### 🐛 Failure Attractors
- Discovered failure patterns with severity indicators (critical/high/medium/low)
- Charts: By Severity (pie), By Model (bar), Top Frequencies (bar)
- Searchable and filterable attractor list
- Expandable example failures for each pattern
- Filter by model and severity

### 📜 History
- Chronological list of all test runs
- **Pass Rate Trend**: Line chart over time
- Filter by run type (test/comparison/regression) and search by model
- Select multiple runs for comparison
- Export history as JSON
- Clear history option

## Quick Start

### Prerequisites
- Node.js 18+ and npm
- Python 3.10+ (for the API backend)

### Frontend Setup

```bash
cd apps/ui
npm install
npm run dev
```

The dashboard runs at **http://localhost:3000**

### Backend Setup (Optional)

The dashboard works standalone with demo data. To connect to the live QA API:

```bash
# Install Python dependencies
pip install fastapi uvicorn pydantic

# Start the API server
cd apps/api
uvicorn main:app --host 0.0.0.0 --port 8000
```

The Vite dev server proxies `/v1/*` and `/health` requests to `http://localhost:8000`.

### Production Build

```bash
cd apps/ui
npm run build     # Outputs to apps/ui/dist/
npm run preview   # Preview production build at port 4173
```

## Architecture

```
apps/ui/
├── index.html              # HTML entry point
├── package.json            # Dependencies (React, Recharts, Tailwind, etc.)
├── vite.config.js          # Vite config with API proxy
├── tailwind.config.js      # Tailwind CSS configuration
├── postcss.config.js       # PostCSS config
└── src/
    ├── main.jsx            # App entry with React Router
    ├── index.css           # Tailwind base + custom styles
    ├── components/
    │   ├── Layout.jsx      # Sidebar navigation + content layout
    │   ├── StatCard.jsx    # Metric display card
    │   ├── StatusBadge.jsx # Status/severity/category badges
    │   ├── EmptyState.jsx  # Empty state placeholder
    │   └── LoadingSpinner.jsx
    ├── pages/
    │   ├── Overview.jsx    # Home dashboard
    │   ├── Configure.jsx   # Test configuration
    │   ├── RunTests.jsx    # Test execution
    │   ├── Results.jsx     # Results visualization
    │   ├── Attractors.jsx  # Failure pattern analysis
    │   └── History.jsx     # Test run history
    └── utils/
        ├── api.js          # API client (connects to FastAPI backend)
        ├── mockData.js     # Demo data for standalone use
        └── storage.js      # localStorage helpers
```

## API Integration

The dashboard connects to these backend endpoints:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | System health check |
| `/v1/qa/models` | GET | List available models |
| `/v1/qa/test-model` | POST | Test a single model |
| `/v1/qa/compare-models` | POST | Compare multiple models |
| `/v1/qa/regression` | POST | Run regression tests |
| `/v1/qa/generate-tests` | POST | Generate test cases |
| `/v1/qa/attractors` | GET | Get all failure attractors |
| `/v1/qa/reports/{id}` | GET | Get a specific report |
| `/v1/qa/comparisons/{id}` | GET | Get a comparison report |

## Example Workflow

1. **Open Dashboard** → Overview shows system status and quick stats
2. **Configure** → Select GPT-4o + Claude 3.5, choose Capability + Safety + Adversarial categories, set 5 tests per category
3. **Run Tests** → Click Start, watch real-time progress
4. **View Results** → See charts: radar, bar, pie charts with category breakdowns
5. **Check Attractors** → Review discovered failure patterns sorted by severity
6. **History** → Track trend over multiple test runs

## Tech Stack

- **React 18** with hooks and functional components
- **React Router v6** for client-side routing
- **Recharts** for data visualization (bar, radar, pie, line charts)
- **Tailwind CSS 3** for responsive, utility-first styling
- **Lucide React** for consistent iconography
- **react-hot-toast** for notifications
- **date-fns** for date formatting
- **Vite 5** for fast development and optimized builds
