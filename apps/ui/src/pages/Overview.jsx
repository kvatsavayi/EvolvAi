import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Activity, CheckCircle2, XCircle, Bug, Cpu, Clock, Zap,
  ArrowRight, Play, Settings, BarChart3, Shield,
} from 'lucide-react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, PieChart, Pie, Cell } from 'recharts';
import StatCard from '../components/StatCard';
import { StatusBadge } from '../components/StatusBadge';
import { fetchHealth, fetchAttractors } from '../utils/api';
import { loadHistory, loadAllResults } from '../utils/storage';
import { MOCK_HISTORY, MOCK_ATTRACTORS } from '../utils/mockData';

const PIE_COLORS = ['#10b981', '#ef4444', '#f59e0b', '#6366f1'];

export default function Overview() {
  const [health, setHealth] = useState({ status: 'checking' });
  const [history, setHistory] = useState([]);
  const [stats, setStats] = useState({ models: 0, passRate: 0, attractors: 0, totalTests: 0 });

  useEffect(() => {
    // Check backend health
    fetchHealth().then(setHealth).catch(() => setHealth({ status: 'offline' }));

    // Load history from localStorage or use mock
    const stored = loadHistory();
    const hist = stored.length > 0 ? stored : MOCK_HISTORY;
    setHistory(hist.slice(0, 5));

    // Compute stats
    const results = loadAllResults();
    const resultValues = Object.values(results);
    if (resultValues.length > 0) {
      const models = new Set();
      let totalPassed = 0, totalTests = 0, totalAttractors = 0;
      resultValues.forEach(r => {
        if (r.model_id) models.add(r.model_id);
        if (r.summary) {
          totalPassed += r.summary.passed || 0;
          totalTests += r.summary.total_tests || 0;
        }
        totalAttractors += (r.attractors || []).length;
      });
      setStats({
        models: models.size || hist.length,
        passRate: totalTests > 0 ? Math.round((totalPassed / totalTests) * 100) : 85,
        attractors: totalAttractors || 6,
        totalTests: totalTests || 65,
      });
    } else {
      setStats({ models: 7, passRate: 85, attractors: 6, totalTests: 65 });
    }
  }, []);

  const categoryData = [
    { name: 'Capability', value: 35, fill: '#6366f1' },
    { name: 'Safety', value: 25, fill: '#10b981' },
    { name: 'Adversarial', value: 20, fill: '#ef4444' },
    { name: 'Regression', value: 20, fill: '#f59e0b' },
  ];

  const passRateData = [
    { model: 'Claude 3.5', rate: 93 },
    { model: 'GPT-4o', rate: 87 },
    { model: 'Gemini 1.5', rate: 82 },
    { model: 'Llama 405B', rate: 80 },
    { model: 'GPT-4o Mini', rate: 75 },
    { model: 'Llama 70B', rate: 70 },
  ];

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-gray-900">QA Dashboard</h2>
          <p className="text-gray-500 mt-1">Monitor and manage LLM quality assurance testing</p>
        </div>
        <div className="flex items-center gap-3">
          <StatusBadge status={health.status === 'ok' ? 'online' : health.status === 'checking' ? 'idle' : 'offline'} />
          <Link to="/run" className="btn-primary">
            <Play className="w-4 h-4" /> Run Tests
          </Link>
        </div>
      </div>

      {/* Stat Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard icon={Cpu} label="Models Available" value={stats.models} color="brand" subtext="Ready for testing" />
        <StatCard icon={CheckCircle2} label="Pass Rate" value={`${stats.passRate}%`} color="green" trend={3} subtext="Across all tests" />
        <StatCard icon={Bug} label="Attractors Found" value={stats.attractors} color="red" subtext="Failure patterns" />
        <StatCard icon={Activity} label="Total Tests Run" value={stats.totalTests} color="blue" subtext="This session" />
      </div>

      {/* Charts Row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Pass Rate Bar Chart */}
        <div className="card">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">Model Pass Rates</h3>
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={passRateData} layout="vertical" margin={{ left: 20 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
              <XAxis type="number" domain={[0, 100]} tick={{ fill: '#64748b', fontSize: 12 }} />
              <YAxis dataKey="model" type="category" width={90} tick={{ fill: '#374151', fontSize: 12 }} />
              <Tooltip
                contentStyle={{ borderRadius: 8, border: '1px solid #e2e8f0', fontSize: 13 }}
                formatter={(v) => [`${v}%`, 'Pass Rate']}
              />
              <Bar dataKey="rate" radius={[0, 6, 6, 0]} maxBarSize={28}>
                {passRateData.map((entry, i) => (
                  <Cell key={i} fill={entry.rate >= 85 ? '#10b981' : entry.rate >= 70 ? '#f59e0b' : '#ef4444'} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Category Pie Chart */}
        <div className="card">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">Test Categories</h3>
          <ResponsiveContainer width="100%" height={280}>
            <PieChart>
              <Pie
                data={categoryData}
                cx="50%"
                cy="50%"
                innerRadius={70}
                outerRadius={110}
                paddingAngle={4}
                dataKey="value"
              >
                {categoryData.map((entry, i) => (
                  <Cell key={i} fill={entry.fill} />
                ))}
              </Pie>
              <Tooltip contentStyle={{ borderRadius: 8, border: '1px solid #e2e8f0', fontSize: 13 }} />
            </PieChart>
          </ResponsiveContainer>
          <div className="flex flex-wrap justify-center gap-4 mt-2">
            {categoryData.map((c) => (
              <div key={c.name} className="flex items-center gap-2 text-sm">
                <div className="w-3 h-3 rounded-full" style={{ backgroundColor: c.fill }} />
                <span className="text-gray-600">{c.name}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Recent Runs + Quick Start */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Recent Runs */}
        <div className="card">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-semibold text-gray-900">Recent Test Runs</h3>
            <Link to="/history" className="text-sm text-brand-600 hover:text-brand-700 font-medium flex items-center gap-1">
              View all <ArrowRight className="w-3.5 h-3.5" />
            </Link>
          </div>
          <div className="space-y-3">
            {history.map((run) => (
              <div key={run.id} className="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
                <div className="flex items-center gap-3">
                  <div className={`w-2 h-2 rounded-full ${run.summary?.pass_rate >= 0.8 || run.summary?.verdict === 'no_regression' ? 'bg-emerald-500' : 'bg-amber-500'}`} />
                  <div>
                    <p className="text-sm font-medium text-gray-900">
                      {run.type === 'test' ? `Test: ${run.model}` : run.type === 'comparison' ? `Compare: ${run.models?.join(' vs ')}` : `Regression: ${run.models?.join(' → ')}`}
                    </p>
                    <p className="text-xs text-gray-500">{new Date(run.timestamp).toLocaleString()}</p>
                  </div>
                </div>
                <span className="text-sm font-medium text-gray-700">
                  {run.summary?.pass_rate ? `${Math.round(run.summary.pass_rate * 100)}%` : run.summary?.verdict || '—'}
                </span>
              </div>
            ))}
            {history.length === 0 && (
              <p className="text-sm text-gray-400 text-center py-4">No test runs yet</p>
            )}
          </div>
        </div>

        {/* Quick Start */}
        <div className="card">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">Quick Start Guide</h3>
          <div className="space-y-4">
            {[
              { step: 1, icon: Settings, title: 'Configure Test Suite', desc: 'Select models and test categories', to: '/configure' },
              { step: 2, icon: Play, title: 'Run Tests', desc: 'Execute QA tests with real-time progress', to: '/run' },
              { step: 3, icon: BarChart3, title: 'View Results', desc: 'Analyze pass rates and model comparisons', to: '/results' },
              { step: 4, icon: Bug, title: 'Review Attractors', desc: 'Discover failure patterns and fix them', to: '/attractors' },
            ].map(({ step, icon: Icon, title, desc, to }) => (
              <Link
                key={step}
                to={to}
                className="flex items-center gap-4 p-3 rounded-lg hover:bg-gray-50 transition-colors group"
              >
                <div className="w-10 h-10 rounded-lg bg-brand-50 text-brand-600 flex items-center justify-center font-bold text-sm group-hover:bg-brand-100">
                  {step}
                </div>
                <div className="flex-1">
                  <p className="text-sm font-semibold text-gray-900 flex items-center gap-2">
                    <Icon className="w-4 h-4 text-gray-400" /> {title}
                  </p>
                  <p className="text-xs text-gray-500 mt-0.5">{desc}</p>
                </div>
                <ArrowRight className="w-4 h-4 text-gray-300 group-hover:text-brand-500 transition-colors" />
              </Link>
            ))}
          </div>

          <div className="mt-6 p-4 bg-brand-50 rounded-lg border border-brand-100">
            <div className="flex items-center gap-2 text-brand-700 font-medium text-sm">
              <Shield className="w-4 h-4" /> Three-Layer Hybrid Evaluation
            </div>
            <p className="text-xs text-brand-600 mt-1">
              Normalizer cleans outputs → Rule-based validators score → LLM Judge provides nuanced evaluation.
              This ensures consistent, fair, and thorough QA testing.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
