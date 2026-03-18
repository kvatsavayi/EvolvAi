import React, { useEffect, useState } from 'react';
import {
  BarChart3, Filter, Download, ChevronDown, ChevronUp,
  CheckCircle2, XCircle, Clock, TrendingUp,
} from 'lucide-react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  RadarChart, Radar, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
  PieChart, Pie, Cell, Legend,
} from 'recharts';
import clsx from 'clsx';
import { CategoryBadge } from '../components/StatusBadge';
import EmptyState from '../components/EmptyState';
import { loadAllResults } from '../utils/storage';
import { MOCK_TEST_RESULT, MOCK_COMPARISON } from '../utils/mockData';

const CHART_COLORS = ['#6366f1', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4', '#f97316'];

export default function Results() {
  const [results, setResults] = useState([]);
  const [selectedResult, setSelectedResult] = useState(null);
  const [filterCategory, setFilterCategory] = useState('all');
  const [filterStatus, setFilterStatus] = useState('all');
  const [expandedTests, setExpandedTests] = useState(new Set());

  useEffect(() => {
    const stored = loadAllResults();
    const entries = Object.entries(stored).map(([id, data]) => ({ id, ...data }));
    if (entries.length > 0) {
      setResults(entries);
      setSelectedResult(entries[0]);
    } else {
      // Use mock data
      const mockEntries = [
        { id: 'mock_test', ...MOCK_TEST_RESULT },
        { id: 'mock_comparison', ...MOCK_COMPARISON },
      ];
      setResults(mockEntries);
      setSelectedResult(mockEntries[0]);
    }
  }, []);

  const report = selectedResult?.report;
  const testResults = report?.results || [];

  const filteredResults = testResults.filter(r => {
    if (filterCategory !== 'all' && r.category !== filterCategory) return false;
    if (filterStatus === 'pass' && !r.passed) return false;
    if (filterStatus === 'fail' && r.passed) return false;
    return true;
  });

  const categories = [...new Set(testResults.map(r => r.category))];

  // Category breakdown for pie chart
  const categoryBreakdown = categories.map((cat, i) => {
    const catResults = testResults.filter(r => r.category === cat);
    return {
      name: cat,
      passed: catResults.filter(r => r.passed).length,
      failed: catResults.filter(r => !r.passed).length,
      total: catResults.length,
      passRate: catResults.length > 0 ? catResults.filter(r => r.passed).length / catResults.length : 0,
    };
  });

  // Radar chart data
  const radarData = categories.map(cat => {
    const catResults = testResults.filter(r => r.category === cat);
    const passRate = catResults.length > 0 ? (catResults.filter(r => r.passed).length / catResults.length) * 100 : 0;
    return { category: cat, score: Math.round(passRate), fullMark: 100 };
  });

  // Pass/fail pie chart
  const passFailData = [
    { name: 'Passed', value: testResults.filter(r => r.passed).length, fill: '#10b981' },
    { name: 'Failed', value: testResults.filter(r => !r.passed).length, fill: '#ef4444' },
  ];

  // Bar chart: pass rate by category
  const barData = categoryBreakdown.map(c => ({
    name: c.name,
    'Pass Rate': Math.round(c.passRate * 100),
    Passed: c.passed,
    Failed: c.failed,
  }));

  // Comparison view
  const isComparison = !!selectedResult?.rankings;
  const comparisonBarData = isComparison
    ? Object.entries(selectedResult.model_summaries || {}).map(([model, data]) => ({
        name: model,
        'Pass Rate': Math.round((data.pass_rate || 0) * 100),
        'Latency (ms)': data.avg_latency_ms || 0,
        Attractors: data.attractors || 0,
      }))
    : [];

  const toggleExpanded = (id) => {
    setExpandedTests(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const exportResults = () => {
    const data = JSON.stringify(selectedResult, null, 2);
    const blob = new Blob([data], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `qa-results-${selectedResult?.report_id || selectedResult?.comparison_id || 'export'}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  if (results.length === 0) {
    return (
      <EmptyState
        icon={BarChart3}
        title="No Results Yet"
        description="Run some tests to see results here. Go to Configure → Run Tests to get started."
      />
    );
  }

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-gray-900">Results Dashboard</h2>
          <p className="text-gray-500 mt-1">Detailed test results and model analysis</p>
        </div>
        <div className="flex items-center gap-2">
          {results.length > 1 && (
            <select
              value={selectedResult?.id || ''}
              onChange={(e) => setSelectedResult(results.find(r => r.id === e.target.value))}
              className="input-field text-sm w-48"
            >
              {results.map(r => (
                <option key={r.id} value={r.id}>
                  {r.model_id || r.comparison_id || r.id}
                </option>
              ))}
            </select>
          )}
          <button onClick={exportResults} className="btn-secondary text-sm">
            <Download className="w-4 h-4" /> Export
          </button>
        </div>
      </div>

      {/* Summary Cards */}
      {selectedResult?.summary && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <div className="card text-center">
            <p className="text-xs text-gray-500">Total Tests</p>
            <p className="text-2xl font-bold text-gray-900 mt-1">{selectedResult.summary.total_tests}</p>
          </div>
          <div className="card text-center">
            <p className="text-xs text-gray-500">Passed</p>
            <p className="text-2xl font-bold text-emerald-600 mt-1">{selectedResult.summary.passed}</p>
          </div>
          <div className="card text-center">
            <p className="text-xs text-gray-500">Failed</p>
            <p className="text-2xl font-bold text-red-600 mt-1">{selectedResult.summary.failed}</p>
          </div>
          <div className="card text-center">
            <p className="text-xs text-gray-500">Avg Latency</p>
            <p className="text-2xl font-bold text-gray-900 mt-1">{selectedResult.summary.avg_latency_ms?.toFixed(0)}ms</p>
          </div>
        </div>
      )}

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {isComparison ? (
          /* Comparison bar chart */
          <div className="card lg:col-span-2">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Model Comparison</h3>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={comparisonBarData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                <XAxis dataKey="name" tick={{ fill: '#374151', fontSize: 12 }} />
                <YAxis tick={{ fill: '#64748b', fontSize: 12 }} />
                <Tooltip contentStyle={{ borderRadius: 8, border: '1px solid #e2e8f0', fontSize: 13 }} />
                <Bar dataKey="Pass Rate" fill="#6366f1" radius={[6, 6, 0, 0]} maxBarSize={50} />
                <Bar dataKey="Attractors" fill="#ef4444" radius={[6, 6, 0, 0]} maxBarSize={50} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <>
            {/* Category bar chart */}
            <div className="card">
              <h3 className="text-lg font-semibold text-gray-900 mb-4">Category Breakdown</h3>
              <ResponsiveContainer width="100%" height={280}>
                <BarChart data={barData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                  <XAxis dataKey="name" tick={{ fill: '#374151', fontSize: 12 }} />
                  <YAxis tick={{ fill: '#64748b', fontSize: 12 }} />
                  <Tooltip contentStyle={{ borderRadius: 8, border: '1px solid #e2e8f0', fontSize: 13 }} />
                  <Bar dataKey="Passed" fill="#10b981" stackId="a" radius={[0, 0, 0, 0]} maxBarSize={40} />
                  <Bar dataKey="Failed" fill="#ef4444" stackId="a" radius={[6, 6, 0, 0]} maxBarSize={40} />
                </BarChart>
              </ResponsiveContainer>
            </div>

            {/* Radar chart */}
            <div className="card">
              <h3 className="text-lg font-semibold text-gray-900 mb-4">Performance Radar</h3>
              <ResponsiveContainer width="100%" height={280}>
                <RadarChart data={radarData}>
                  <PolarGrid stroke="#e2e8f0" />
                  <PolarAngleAxis dataKey="category" tick={{ fill: '#374151', fontSize: 12 }} />
                  <PolarRadiusAxis angle={30} domain={[0, 100]} tick={{ fill: '#64748b', fontSize: 10 }} />
                  <Radar name="Score" dataKey="score" stroke="#6366f1" fill="#6366f1" fillOpacity={0.3} strokeWidth={2} />
                </RadarChart>
              </ResponsiveContainer>
            </div>

            {/* Pass/Fail pie */}
            <div className="card">
              <h3 className="text-lg font-semibold text-gray-900 mb-4">Pass/Fail Distribution</h3>
              <ResponsiveContainer width="100%" height={240}>
                <PieChart>
                  <Pie data={passFailData} cx="50%" cy="50%" innerRadius={60} outerRadius={90} paddingAngle={4} dataKey="value">
                    {passFailData.map((entry, i) => (
                      <Cell key={i} fill={entry.fill} />
                    ))}
                  </Pie>
                  <Tooltip contentStyle={{ borderRadius: 8, border: '1px solid #e2e8f0', fontSize: 13 }} />
                  <Legend />
                </PieChart>
              </ResponsiveContainer>
            </div>

            {/* Latency distribution */}
            <div className="card">
              <h3 className="text-lg font-semibold text-gray-900 mb-4">Latency by Category</h3>
              <ResponsiveContainer width="100%" height={240}>
                <BarChart data={categories.map((cat, i) => {
                  const catResults = testResults.filter(r => r.category === cat);
                  const avgLat = catResults.reduce((sum, r) => sum + (r.latency_ms || 0), 0) / (catResults.length || 1);
                  return { name: cat, 'Avg Latency (ms)': Math.round(avgLat) };
                })}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                  <XAxis dataKey="name" tick={{ fill: '#374151', fontSize: 12 }} />
                  <YAxis tick={{ fill: '#64748b', fontSize: 12 }} />
                  <Tooltip contentStyle={{ borderRadius: 8, border: '1px solid #e2e8f0', fontSize: 13 }} />
                  <Bar dataKey="Avg Latency (ms)" fill="#f59e0b" radius={[6, 6, 0, 0]} maxBarSize={50} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </>
        )}
      </div>

      {/* Detailed Results Table */}
      {testResults.length > 0 && (
        <div className="card">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-4">
            <h3 className="text-lg font-semibold text-gray-900">Detailed Test Cases</h3>
            <div className="flex items-center gap-2">
              <select
                value={filterCategory}
                onChange={(e) => setFilterCategory(e.target.value)}
                className="input-field text-sm w-36"
              >
                <option value="all">All Categories</option>
                {categories.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
              <select
                value={filterStatus}
                onChange={(e) => setFilterStatus(e.target.value)}
                className="input-field text-sm w-28"
              >
                <option value="all">All</option>
                <option value="pass">Passed</option>
                <option value="fail">Failed</option>
              </select>
            </div>
          </div>

          <div className="space-y-2 max-h-[600px] overflow-y-auto">
            {filteredResults.map((result) => (
              <div key={result.test_id} className="border border-gray-100 rounded-lg overflow-hidden">
                <button
                  onClick={() => toggleExpanded(result.test_id)}
                  className="w-full flex items-center gap-3 p-3 hover:bg-gray-50 transition-colors text-left"
                >
                  {result.passed ? (
                    <CheckCircle2 className="w-5 h-5 text-emerald-500 flex-shrink-0" />
                  ) : (
                    <XCircle className="w-5 h-5 text-red-500 flex-shrink-0" />
                  )}
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-gray-900 truncate">{result.prompt}</p>
                  </div>
                  <CategoryBadge category={result.category} />
                  {result.latency_ms && (
                    <span className="text-xs text-gray-400 flex-shrink-0 flex items-center gap-1">
                      <Clock className="w-3 h-3" />{result.latency_ms}ms
                    </span>
                  )}
                  {expandedTests.has(result.test_id)
                    ? <ChevronUp className="w-4 h-4 text-gray-400" />
                    : <ChevronDown className="w-4 h-4 text-gray-400" />
                  }
                </button>
                {expandedTests.has(result.test_id) && (
                  <div className="px-4 pb-4 pt-2 border-t border-gray-100 bg-gray-50">
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                      <div>
                        <p className="text-xs font-medium text-gray-500 mb-2">Scores</p>
                        {result.scores && Object.entries(result.scores).map(([k, v]) => (
                          <div key={k} className="flex justify-between items-center mb-1">
                            <span className="text-xs text-gray-600 capitalize">{k}</span>
                            <div className="flex items-center gap-2">
                              <div className="w-20 h-1.5 bg-gray-200 rounded-full overflow-hidden">
                                <div
                                  className={clsx('h-full rounded-full', v >= 0.7 ? 'bg-emerald-500' : v >= 0.4 ? 'bg-amber-500' : 'bg-red-500')}
                                  style={{ width: `${(typeof v === 'number' ? v : 0) * 100}%` }}
                                />
                              </div>
                              <span className="text-xs font-medium text-gray-700 w-10 text-right">
                                {typeof v === 'number' ? (v * 100).toFixed(0) + '%' : v}
                              </span>
                            </div>
                          </div>
                        ))}
                      </div>
                      {result.failures && result.failures.length > 0 && (
                        <div>
                          <p className="text-xs font-medium text-gray-500 mb-2">Failure Details</p>
                          {result.failures.map((f, i) => (
                            <div key={i} className="flex items-start gap-2 mb-1">
                              <XCircle className="w-3.5 h-3.5 text-red-400 mt-0.5 flex-shrink-0" />
                              <p className="text-xs text-red-700">{f}</p>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
          <p className="text-xs text-gray-400 mt-3">
            Showing {filteredResults.length} of {testResults.length} tests
          </p>
        </div>
      )}
    </div>
  );
}
