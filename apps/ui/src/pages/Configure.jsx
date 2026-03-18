import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Settings, Save, Upload, RotateCcw, ChevronDown, ChevronUp,
  Check, Info, Cpu,
} from 'lucide-react';
import clsx from 'clsx';
import toast from 'react-hot-toast';
import { fetchModels } from '../utils/api';
import { saveConfig, loadConfig } from '../utils/storage';
import { MOCK_MODELS } from '../utils/mockData';

const CATEGORIES = [
  { id: 'capability', label: 'Capability', desc: 'Tests reasoning, coding, math, language understanding', color: 'blue' },
  { id: 'safety', label: 'Safety', desc: 'Tests harmful content refusal, bias, privacy', color: 'green' },
  { id: 'adversarial', label: 'Adversarial', desc: 'Tests jailbreaks, prompt injection, manipulation', color: 'red' },
  { id: 'regression', label: 'Regression', desc: 'Tests known failure cases and previous issues', color: 'purple' },
];

const DEFAULT_CONFIG = {
  selectedModels: ['GPT4O', 'CLAUDE_V3_5_SONNET'],
  selectedCategories: ['capability', 'safety', 'adversarial'],
  countPerCategory: 5,
  difficulty: null,
  useLlmJudge: false,
  judgeModel: null,
  consistencyRuns: 1,
  useNormalization: true,
  customPrompts: '',
};

export default function Configure() {
  const navigate = useNavigate();
  const [models, setModels] = useState([]);
  const [config, setConfig] = useState(DEFAULT_CONFIG);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [configName, setConfigName] = useState('');

  useEffect(() => {
    fetchModels()
      .then(data => setModels(data.models || []))
      .catch(() => setModels(MOCK_MODELS));

    const saved = loadConfig();
    if (saved) setConfig({ ...DEFAULT_CONFIG, ...saved });
  }, []);

  const toggleModel = (id) => {
    setConfig(prev => ({
      ...prev,
      selectedModels: prev.selectedModels.includes(id)
        ? prev.selectedModels.filter(m => m !== id)
        : [...prev.selectedModels, id],
    }));
  };

  const toggleCategory = (id) => {
    setConfig(prev => ({
      ...prev,
      selectedCategories: prev.selectedCategories.includes(id)
        ? prev.selectedCategories.filter(c => c !== id)
        : [...prev.selectedCategories, id],
    }));
  };

  const handleSave = () => {
    saveConfig(config);
    toast.success('Configuration saved');
  };

  const handleReset = () => {
    setConfig(DEFAULT_CONFIG);
    toast.success('Configuration reset to defaults');
  };

  const handleRunTests = () => {
    saveConfig(config);
    navigate('/run');
  };

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-gray-900">Test Configuration</h2>
          <p className="text-gray-500 mt-1">Configure models, test suites, and evaluation parameters</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={handleReset} className="btn-ghost text-sm">
            <RotateCcw className="w-4 h-4" /> Reset
          </button>
          <button onClick={handleSave} className="btn-secondary text-sm">
            <Save className="w-4 h-4" /> Save
          </button>
          <button onClick={handleRunTests} className="btn-primary text-sm">
            Run Tests →
          </button>
        </div>
      </div>

      {/* Model Selection */}
      <div className="card">
        <div className="flex items-center gap-2 mb-4">
          <Cpu className="w-5 h-5 text-brand-600" />
          <h3 className="text-lg font-semibold text-gray-900">Model Selection</h3>
          <span className="badge-blue">{config.selectedModels.length} selected</span>
        </div>
        <p className="text-sm text-gray-500 mb-4">Choose which models to test. Select multiple for comparison.</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
          {models.map(model => {
            const selected = config.selectedModels.includes(model.id);
            return (
              <button
                key={model.id}
                onClick={() => toggleModel(model.id)}
                className={clsx(
                  'text-left p-4 rounded-lg border-2 transition-all',
                  selected
                    ? 'border-brand-500 bg-brand-50 ring-1 ring-brand-200'
                    : 'border-gray-200 hover:border-gray-300 bg-white'
                )}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="text-sm font-semibold text-gray-900">{model.id}</span>
                  {selected && <Check className="w-4 h-4 text-brand-600" />}
                </div>
                <p className="text-xs text-gray-500">{model.description}</p>
              </button>
            );
          })}
        </div>
      </div>

      {/* Test Suite Selection */}
      <div className="card">
        <h3 className="text-lg font-semibold text-gray-900 mb-4">Test Suite Selection</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {CATEGORIES.map(cat => {
            const selected = config.selectedCategories.includes(cat.id);
            const colors = {
              blue: { border: 'border-blue-500 bg-blue-50', dot: 'bg-blue-500' },
              green: { border: 'border-emerald-500 bg-emerald-50', dot: 'bg-emerald-500' },
              red: { border: 'border-red-500 bg-red-50', dot: 'bg-red-500' },
              purple: { border: 'border-purple-500 bg-purple-50', dot: 'bg-purple-500' },
            };
            return (
              <button
                key={cat.id}
                onClick={() => toggleCategory(cat.id)}
                className={clsx(
                  'text-left p-4 rounded-lg border-2 transition-all',
                  selected ? colors[cat.color].border : 'border-gray-200 hover:border-gray-300 bg-white'
                )}
              >
                <div className="flex items-center gap-3">
                  <div className={clsx('w-3 h-3 rounded-full', selected ? colors[cat.color].dot : 'bg-gray-300')} />
                  <div>
                    <p className="text-sm font-semibold text-gray-900">{cat.label}</p>
                    <p className="text-xs text-gray-500 mt-0.5">{cat.desc}</p>
                  </div>
                  {selected && <Check className="w-4 h-4 ml-auto text-gray-600" />}
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* Test Count */}
      <div className="card">
        <h3 className="text-lg font-semibold text-gray-900 mb-4">Test Parameters</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Tests Per Category</label>
            <input
              type="number"
              min={1}
              max={50}
              value={config.countPerCategory}
              onChange={(e) => setConfig(prev => ({ ...prev, countPerCategory: parseInt(e.target.value) || 5 }))}
              className="input-field"
            />
            <p className="text-xs text-gray-400 mt-1">Total: ~{config.countPerCategory * config.selectedCategories.length} tests per model</p>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Difficulty</label>
            <select
              value={config.difficulty || ''}
              onChange={(e) => setConfig(prev => ({ ...prev, difficulty: e.target.value || null }))}
              className="input-field"
            >
              <option value="">Auto (mixed)</option>
              <option value="easy">Easy</option>
              <option value="medium">Medium</option>
              <option value="hard">Hard</option>
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Consistency Runs</label>
            <input
              type="number"
              min={1}
              max={5}
              value={config.consistencyRuns}
              onChange={(e) => setConfig(prev => ({ ...prev, consistencyRuns: parseInt(e.target.value) || 1 }))}
              className="input-field"
            />
            <p className="text-xs text-gray-400 mt-1">Run each test N times to check consistency</p>
          </div>
        </div>
      </div>

      {/* Advanced Options */}
      <div className="card">
        <button
          onClick={() => setShowAdvanced(!showAdvanced)}
          className="flex items-center justify-between w-full"
        >
          <h3 className="text-lg font-semibold text-gray-900">Advanced Options</h3>
          {showAdvanced ? <ChevronUp className="w-5 h-5 text-gray-400" /> : <ChevronDown className="w-5 h-5 text-gray-400" />}
        </button>
        {showAdvanced && (
          <div className="mt-4 space-y-6">
            <div className="flex items-center justify-between p-4 bg-gray-50 rounded-lg">
              <div>
                <p className="text-sm font-medium text-gray-900">Response Normalization</p>
                <p className="text-xs text-gray-500">Clean and normalize model responses before evaluation</p>
              </div>
              <button
                onClick={() => setConfig(prev => ({ ...prev, useNormalization: !prev.useNormalization }))}
                className={clsx(
                  'relative w-12 h-6 rounded-full transition-colors',
                  config.useNormalization ? 'bg-brand-600' : 'bg-gray-300'
                )}
              >
                <span className={clsx(
                  'absolute top-0.5 w-5 h-5 bg-white rounded-full shadow transition-transform',
                  config.useNormalization ? 'left-6' : 'left-0.5'
                )} />
              </button>
            </div>

            <div className="flex items-center justify-between p-4 bg-gray-50 rounded-lg">
              <div>
                <p className="text-sm font-medium text-gray-900">LLM-as-Judge</p>
                <p className="text-xs text-gray-500">Use an LLM to provide nuanced evaluation scores</p>
              </div>
              <button
                onClick={() => setConfig(prev => ({ ...prev, useLlmJudge: !prev.useLlmJudge }))}
                className={clsx(
                  'relative w-12 h-6 rounded-full transition-colors',
                  config.useLlmJudge ? 'bg-brand-600' : 'bg-gray-300'
                )}
              >
                <span className={clsx(
                  'absolute top-0.5 w-5 h-5 bg-white rounded-full shadow transition-transform',
                  config.useLlmJudge ? 'left-6' : 'left-0.5'
                )} />
              </button>
            </div>

            {config.useLlmJudge && (
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">Judge Model</label>
                <select
                  value={config.judgeModel || ''}
                  onChange={(e) => setConfig(prev => ({ ...prev, judgeModel: e.target.value || null }))}
                  className="input-field"
                >
                  <option value="">Default (Claude 3.5 Sonnet)</option>
                  {models.map(m => <option key={m.id} value={m.id}>{m.description}</option>)}
                </select>
              </div>
            )}

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">Custom Test Prompts</label>
              <textarea
                rows={4}
                value={config.customPrompts}
                onChange={(e) => setConfig(prev => ({ ...prev, customPrompts: e.target.value }))}
                className="input-field font-mono text-sm"
                placeholder="One prompt per line. These will be added as custom tests."
              />
              <p className="text-xs text-gray-400 mt-1">Optional. Add your own test prompts in addition to generated ones.</p>
            </div>
          </div>
        )}
      </div>

      {/* Config Summary */}
      <div className="card bg-gray-50 border-gray-200">
        <div className="flex items-center gap-2 mb-3">
          <Info className="w-4 h-4 text-gray-500" />
          <h4 className="text-sm font-semibold text-gray-700">Configuration Summary</h4>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm">
          <div>
            <p className="text-gray-500">Models</p>
            <p className="font-medium text-gray-900">{config.selectedModels.length}</p>
          </div>
          <div>
            <p className="text-gray-500">Categories</p>
            <p className="font-medium text-gray-900">{config.selectedCategories.length}</p>
          </div>
          <div>
            <p className="text-gray-500">Tests/Category</p>
            <p className="font-medium text-gray-900">{config.countPerCategory}</p>
          </div>
          <div>
            <p className="text-gray-500">Total Tests</p>
            <p className="font-medium text-gray-900">
              ~{config.countPerCategory * config.selectedCategories.length * config.selectedModels.length}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
