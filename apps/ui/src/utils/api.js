/**
 * API client for QA Dashboard
 * Connects to the FastAPI backend at /v1/qa/*
 */

const API_BASE = '/v1/qa';

class ApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

async function request(endpoint, options = {}) {
  const url = `${API_BASE}${endpoint}`;
  const config = {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  };

  try {
    const res = await fetch(url, config);
    const data = await res.json();
    if (!res.ok) {
      throw new ApiError(data.detail || 'Request failed', res.status, data);
    }
    return data;
  } catch (err) {
    if (err instanceof ApiError) throw err;
    throw new ApiError(err.message || 'Network error', 0, null);
  }
}

// ---- Models ----
export async function fetchModels() {
  return request('/models');
}

// ---- Test Model ----
export async function testModel(payload) {
  return request('/test-model', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

// ---- Compare Models ----
export async function compareModels(payload) {
  return request('/compare-models', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

// ---- Regression ----
export async function runRegression(payload) {
  return request('/regression', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

// ---- Generate Tests ----
export async function generateTests(payload) {
  return request('/generate-tests', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

// ---- Reports ----
export async function fetchReport(reportId) {
  return request(`/reports/${encodeURIComponent(reportId)}`);
}

// ---- Comparisons ----
export async function fetchComparison(comparisonId) {
  return request(`/comparisons/${encodeURIComponent(comparisonId)}`);
}

// ---- Attractors ----
export async function fetchAttractors() {
  return request('/attractors');
}

export async function fetchModelAttractors(modelId) {
  return request(`/attractors/${encodeURIComponent(modelId)}`);
}

// ---- Health ----
export async function fetchHealth() {
  try {
    const res = await fetch('/health');
    return await res.json();
  } catch {
    return { status: 'offline' };
  }
}

export { ApiError };
