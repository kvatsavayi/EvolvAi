/**
 * Local storage helpers for persisting test configurations and history.
 */

const KEYS = {
  CONFIG: 'evolvai_qa_config',
  HISTORY: 'evolvai_qa_history',
  RESULTS: 'evolvai_qa_results',
};

export function saveConfig(config) {
  try {
    localStorage.setItem(KEYS.CONFIG, JSON.stringify(config));
  } catch { /* ignore */ }
}

export function loadConfig() {
  try {
    const raw = localStorage.getItem(KEYS.CONFIG);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function saveToHistory(entry) {
  try {
    const history = loadHistory();
    history.unshift({ ...entry, timestamp: new Date().toISOString() });
    // Keep last 100 entries
    localStorage.setItem(KEYS.HISTORY, JSON.stringify(history.slice(0, 100)));
  } catch { /* ignore */ }
}

export function loadHistory() {
  try {
    const raw = localStorage.getItem(KEYS.HISTORY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

export function clearHistory() {
  localStorage.removeItem(KEYS.HISTORY);
}

export function saveResult(id, result) {
  try {
    const results = loadAllResults();
    results[id] = result;
    // Keep last 50 results
    const keys = Object.keys(results);
    if (keys.length > 50) {
      delete results[keys[0]];
    }
    localStorage.setItem(KEYS.RESULTS, JSON.stringify(results));
  } catch { /* ignore */ }
}

export function loadResult(id) {
  const results = loadAllResults();
  return results[id] || null;
}

export function loadAllResults() {
  try {
    const raw = localStorage.getItem(KEYS.RESULTS);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}
