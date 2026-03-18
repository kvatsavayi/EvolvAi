import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  History as HistoryIcon, Trash2, Download, Search,
  CheckCircle2, XCircle, GitCompare, BarChart3,
  TrendingUp, Calendar, ArrowRight,
} from 'lucide-react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts';
import clsx from 'clsx';
import { format } from 'date-fns';
import { CategoryBadge } from '../components/StatusBadge';
import EmptyState from '../components/EmptyState';
import { loadHistory, clearHistory, loadResult } from '../utils/storage';
import { MOCK_HISTORY } from '../utils/mockData';

export default function History() {
  const navigate = useNavigate();
  const [history, setHistory] = useState([]);
  const [filterType, setFilterType] = useState('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedRuns, setSelectedRuns] = useState(new Set());

  useEffect(() => {
    const stored = loadHistory();
    setHistory(stored.length > 0 ? stored : MOCK_HISTORY);
  }, []);

  const filtered = history
    .filter(h => filterType === 'all' || h.type === filterType)
    .filter(h => {
      if (!searchQuery) return true;
      const q = searchQuery.toLowerCase();
      return (h.model || '').toLowerCase().includes(q) ||
             (h.models || []).some(m => m.toLowerCase().includes(q)) ||
             h.type.includes(q);
    });

  // Trend data for line chart
  const trendData = [...history]
    .reverse()
    .filter(h => h.summary?.pass_rate != null)
    .map((h, i) => ({
      name: format(new Date(h.timestamp), 'MM/dd HH:mm'),
      'Pass Rate': Math.round((h.summary.pass_rate || 0) * 100),
      index: i,
    }));

  const toggleSelect = (id) => {
    setSelectedRuns(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const handleClearHistory = () => {
    if (window.confirm('Clear all test history? This cannot be undone.')) {
      clearHistory();
      setHistory([]);
    }
  };

  const handleExportHistory = () => {
    const data = JSON.stringify(history, null, 2);
    const blob = new Blob([data], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `qa-history-${format(new Date(), 'yyyy-MM-dd')}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleCompare = () => {
    if (selectedRuns.size < 2) return;
    // Navigate to results with comparison mode
    navigate('/results');
  };

  const typeIcons = {
    test: CheckCircle2,
    comparison: GitCompare,
    regression: TrendingUp,
  };

  const typeColors = {
    test: 'text-blue-600 bg-blue-50',
    comparison: 'text-purple-600 bg-purple-50',
    regression: 'text-amber-600 bg-amber-50',
  };

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-gray-900">Test History</h2>
          <p className="text-gray-500 mt-1">{history.length} runs recorded</p>
        </div>
        <div className="flex items-center gap-2">
          {selectedRuns.size >= 2 && (
            <button onClick={handleCompare} className="btn-primary text-sm">
              <GitCompare className="w-4 h-4" /> Compare ({selectedRuns.size})
            </button>
          )}
          <button onClick={handleExportHistory} className="btn-secondary text-sm">
            <Download className="w-4 h-4" /> Export
          </button>
          <button onClick={handleClearHistory} className="btn-ghost text-sm text-red-600">
            <Trash2 className="w-4 h-4" /> Clear
          </button>
        </div>
      </div>

      {/* Trend Chart */}
      {trendData.length >= 2 && (
        <div className="card">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">Pass Rate Trend</h3>
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={trendData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
              <XAxis dataKey="name" tick={{ fill: '#64748b', fontSize: 11 }} />
              <YAxis domain={[0, 100]} tick={{ fill: '#64748b', fontSize: 11 }} />
              <Tooltip contentStyle={{ borderRadius: 8, border: '1px solid #e2e8f0', fontSize: 12 }} />
              <Line type="monotone" dataKey="Pass Rate" stroke="#6366f1" strokeWidth={2.5} dot={{ fill: '#6366f1', r: 4 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-col sm:flex-row gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search by model name..."
            className="input-field pl-9 text-sm"
          />
        </div>
        <select value={filterType} onChange={(e) => setFilterType(e.target.value)} className="input-field text-sm w-36">
          <option value="all">All Types</option>
          <option value="test">Tests</option>
          <option value="comparison">Comparisons</option>
          <option value="regression">Regressions</option>
        </select>
      </div>

      {/* History List */}
      {filtered.length > 0 ? (
        <div className="space-y-3">
          {filtered.map((run) => {
            const Icon = typeIcons[run.type] || CheckCircle2;
            const isSelected = selectedRuns.has(run.id);
            return (
              <div
                key={run.id}
                className={clsx(
                  'card transition-all cursor-pointer',
                  isSelected ? 'ring-2 ring-brand-500 border-brand-300' : 'hover:shadow-md'
                )}
                onClick={() => toggleSelect(run.id)}
              >
                <div className="flex items-center gap-4">
                  <div className={clsx('w-10 h-10 rounded-lg flex items-center justify-center', typeColors[run.type])}>
                    <Icon className="w-5 h-5" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="text-sm font-semibold text-gray-900">
                        {run.type === 'test' ? `Model Test: ${run.model}` :
                         run.type === 'comparison' ? `Compare: ${(run.models || []).join(' vs ')}` :
                         `Regression: ${(run.models || []).join(' → ')}`}
                      </p>
                      <span className="badge-gray text-[10px]">{run.type}</span>
                    </div>
                    <div className="flex items-center gap-3 mt-1">
                      <span className="text-xs text-gray-500 flex items-center gap-1">
                        <Calendar className="w-3 h-3" />
                        {format(new Date(run.timestamp), 'MMM dd, yyyy HH:mm')}
                      </span>
                      <div className="flex gap-1">
                        {(run.categories || []).map(c => <CategoryBadge key={c} category={c} />)}
                      </div>
                    </div>
                  </div>
                  <div className="text-right flex-shrink-0">
                    {run.summary?.pass_rate != null && (
                      <p className={clsx(
                        'text-lg font-bold',
                        run.summary.pass_rate >= 0.8 ? 'text-emerald-600' : run.summary.pass_rate >= 0.6 ? 'text-amber-600' : 'text-red-600'
                      )}>
                        {Math.round(run.summary.pass_rate * 100)}%
                      </p>
                    )}
                    {run.summary?.total_tests && (
                      <p className="text-xs text-gray-500">{run.summary.total_tests} tests</p>
                    )}
                    {run.summary?.verdict && (
                      <p className={clsx(
                        'text-sm font-medium',
                        run.summary.verdict === 'no_regression' ? 'text-emerald-600' : 'text-red-600'
                      )}>
                        {run.summary.verdict === 'no_regression' ? '✓ No regression' : '✗ Regression'}
                      </p>
                    )}
                    {run.summary?.best_model && (
                      <p className="text-xs text-gray-500">Best: {run.summary.best_model}</p>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <EmptyState
          icon={HistoryIcon}
          title="No History Found"
          description={searchQuery || filterType !== 'all' ? 'No runs match your filters.' : 'Run some tests to build history.'}
        />
      )}
    </div>
  );
}
