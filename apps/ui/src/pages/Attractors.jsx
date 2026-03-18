import React, { useEffect, useState } from 'react';
import {
  Bug, Filter, AlertTriangle, ChevronDown, ChevronUp,
  Search, BarChart3, Shield, Zap, XCircle,
} from 'lucide-react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell,
} from 'recharts';
import clsx from 'clsx';
import { SeverityBadge, CategoryBadge } from '../components/StatusBadge';
import EmptyState from '../components/EmptyState';
import { fetchAttractors } from '../utils/api';
import { MOCK_ATTRACTORS } from '../utils/mockData';

const SEVERITY_ORDER = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };
const SEVERITY_COLORS = { critical: '#dc2626', high: '#ef4444', medium: '#f59e0b', low: '#3b82f6', info: '#9ca3af' };

export default function Attractors() {
  const [data, setData] = useState(null);
  const [filterModel, setFilterModel] = useState('all');
  const [filterSeverity, setFilterSeverity] = useState('all');
  const [expanded, setExpanded] = useState(new Set());
  const [searchQuery, setSearchQuery] = useState('');

  useEffect(() => {
    fetchAttractors()
      .then(d => {
        // If API returns empty attractors, use mock data for demo
        if (!d || d.total_attractors === 0) setData(MOCK_ATTRACTORS);
        else setData(d);
      })
      .catch(() => setData(MOCK_ATTRACTORS));
  }, []);

  if (!data) return null;

  const models = Object.keys(data.attractors_by_model || {});
  const allAttractors = models.flatMap(model =>
    (data.attractors_by_model[model] || []).map(a => ({ ...a, model }))
  );

  const filtered = allAttractors
    .filter(a => filterModel === 'all' || a.model === filterModel)
    .filter(a => filterSeverity === 'all' || a.severity === filterSeverity)
    .filter(a => !searchQuery || a.pattern.toLowerCase().includes(searchQuery.toLowerCase()))
    .sort((a, b) => (SEVERITY_ORDER[a.severity] || 9) - (SEVERITY_ORDER[b.severity] || 9));

  // Charts data
  const severityData = ['critical', 'high', 'medium', 'low'].map(s => ({
    name: s,
    count: allAttractors.filter(a => a.severity === s).length,
    fill: SEVERITY_COLORS[s],
  })).filter(d => d.count > 0);

  const modelData = models.map(m => ({
    name: m,
    attractors: (data.attractors_by_model[m] || []).length,
  }));

  const freqData = allAttractors.map(a => ({
    name: a.pattern.length > 25 ? a.pattern.slice(0, 25) + '...' : a.pattern,
    frequency: a.frequency || 1,
    model: a.model,
  })).sort((a, b) => b.frequency - a.frequency).slice(0, 8);

  const toggleExpanded = (idx) => {
    setExpanded(prev => {
      const next = new Set(prev);
      next.has(idx) ? next.delete(idx) : next.add(idx);
      return next;
    });
  };

  if (allAttractors.length === 0) {
    return (
      <EmptyState
        icon={Bug}
        title="No Attractors Discovered"
        description="Run tests to discover failure patterns. Attractors represent recurring failure modes across test runs."
      />
    );
  }

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-gray-900">Failure Attractors</h2>
          <p className="text-gray-500 mt-1">
            {allAttractors.length} patterns discovered across {models.length} models
          </p>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <div className="card text-center">
          <Bug className="w-6 h-6 text-red-500 mx-auto" />
          <p className="text-2xl font-bold text-gray-900 mt-2">{allAttractors.length}</p>
          <p className="text-xs text-gray-500">Total Attractors</p>
        </div>
        <div className="card text-center">
          <AlertTriangle className="w-6 h-6 text-red-500 mx-auto" />
          <p className="text-2xl font-bold text-red-600 mt-2">
            {allAttractors.filter(a => a.severity === 'high' || a.severity === 'critical').length}
          </p>
          <p className="text-xs text-gray-500">High/Critical</p>
        </div>
        <div className="card text-center">
          <Shield className="w-6 h-6 text-amber-500 mx-auto" />
          <p className="text-2xl font-bold text-amber-600 mt-2">
            {allAttractors.filter(a => a.category === 'adversarial' || a.category === 'safety').length}
          </p>
          <p className="text-xs text-gray-500">Security-Related</p>
        </div>
        <div className="card text-center">
          <Zap className="w-6 h-6 text-brand-500 mx-auto" />
          <p className="text-2xl font-bold text-gray-900 mt-2">
            {allAttractors.reduce((sum, a) => sum + (a.frequency || 1), 0)}
          </p>
          <p className="text-xs text-gray-500">Total Occurrences</p>
        </div>
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="card">
          <h3 className="text-sm font-semibold text-gray-900 mb-3">By Severity</h3>
          <ResponsiveContainer width="100%" height={200}>
            <PieChart>
              <Pie data={severityData} cx="50%" cy="50%" innerRadius={40} outerRadius={70} paddingAngle={4} dataKey="count">
                {severityData.map((entry, i) => (
                  <Cell key={i} fill={entry.fill} />
                ))}
              </Pie>
              <Tooltip contentStyle={{ borderRadius: 8, border: '1px solid #e2e8f0', fontSize: 12 }} />
            </PieChart>
          </ResponsiveContainer>
          <div className="flex flex-wrap justify-center gap-3 mt-2">
            {severityData.map(d => (
              <div key={d.name} className="flex items-center gap-1.5 text-xs">
                <div className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: d.fill }} />
                <span className="text-gray-600 capitalize">{d.name} ({d.count})</span>
              </div>
            ))}
          </div>
        </div>

        <div className="card">
          <h3 className="text-sm font-semibold text-gray-900 mb-3">By Model</h3>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={modelData} layout="vertical" margin={{ left: 10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
              <XAxis type="number" tick={{ fill: '#64748b', fontSize: 11 }} />
              <YAxis dataKey="name" type="category" width={80} tick={{ fill: '#374151', fontSize: 11 }} />
              <Tooltip contentStyle={{ borderRadius: 8, border: '1px solid #e2e8f0', fontSize: 12 }} />
              <Bar dataKey="attractors" fill="#ef4444" radius={[0, 6, 6, 0]} maxBarSize={20} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="card">
          <h3 className="text-sm font-semibold text-gray-900 mb-3">Top Frequencies</h3>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={freqData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
              <XAxis dataKey="name" tick={{ fill: '#374151', fontSize: 9 }} angle={-15} />
              <YAxis tick={{ fill: '#64748b', fontSize: 11 }} />
              <Tooltip contentStyle={{ borderRadius: 8, border: '1px solid #e2e8f0', fontSize: 12 }} />
              <Bar dataKey="frequency" fill="#f59e0b" radius={[6, 6, 0, 0]} maxBarSize={30} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Filters */}
      <div className="card">
        <div className="flex flex-col sm:flex-row sm:items-center gap-3 mb-4">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search patterns..."
              className="input-field pl-9 text-sm"
            />
          </div>
          <select value={filterModel} onChange={(e) => setFilterModel(e.target.value)} className="input-field text-sm w-40">
            <option value="all">All Models</option>
            {models.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
          <select value={filterSeverity} onChange={(e) => setFilterSeverity(e.target.value)} className="input-field text-sm w-32">
            <option value="all">All Severity</option>
            <option value="critical">Critical</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
        </div>

        {/* Attractor List */}
        <div className="space-y-3">
          {filtered.map((attractor, idx) => (
            <div key={idx} className={clsx(
              'border rounded-lg overflow-hidden',
              attractor.severity === 'high' || attractor.severity === 'critical'
                ? 'border-red-200 bg-red-50/30'
                : 'border-gray-100'
            )}>
              <button
                onClick={() => toggleExpanded(idx)}
                className="w-full flex items-center gap-3 p-4 hover:bg-gray-50/50 transition-colors text-left"
              >
                <AlertTriangle className={clsx(
                  'w-5 h-5 flex-shrink-0',
                  attractor.severity === 'critical' || attractor.severity === 'high' ? 'text-red-500' :
                  attractor.severity === 'medium' ? 'text-amber-500' : 'text-blue-500'
                )} />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-gray-900">{attractor.pattern}</p>
                  <p className="text-xs text-gray-500 mt-0.5">Model: {attractor.model} · Frequency: {attractor.frequency || 1}x</p>
                </div>
                <SeverityBadge severity={attractor.severity} />
                <CategoryBadge category={attractor.category} />
                {expanded.has(idx) ? <ChevronUp className="w-4 h-4 text-gray-400" /> : <ChevronDown className="w-4 h-4 text-gray-400" />}
              </button>
              {expanded.has(idx) && (
                <div className="px-4 pb-4 pt-2 border-t border-gray-100">
                  <p className="text-xs font-medium text-gray-500 mb-2">Example Failures:</p>
                  <div className="space-y-2">
                    {(attractor.examples || []).map((ex, i) => (
                      <div key={i} className="flex items-start gap-2 p-2 bg-white rounded border border-gray-100">
                        <XCircle className="w-3.5 h-3.5 text-red-400 mt-0.5 flex-shrink-0" />
                        <p className="text-xs text-gray-700">{ex}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
        <p className="text-xs text-gray-400 mt-3">
          Showing {filtered.length} of {allAttractors.length} attractors
        </p>
      </div>
    </div>
  );
}
