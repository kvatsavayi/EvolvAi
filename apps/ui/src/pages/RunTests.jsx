import React, { useState, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Play, Square, Clock, CheckCircle2, XCircle, Loader2,
  AlertTriangle, ChevronDown, ChevronUp,
} from 'lucide-react';
import clsx from 'clsx';
import toast from 'react-hot-toast';
import { testModel, compareModels } from '../utils/api';
import { loadConfig, saveToHistory, saveResult } from '../utils/storage';
import { MOCK_TEST_RESULT, MOCK_COMPARISON } from '../utils/mockData';
import { CategoryBadge } from '../components/StatusBadge';

export default function RunTests() {
  const navigate = useNavigate();
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState({ current: 0, total: 0, phase: '', model: '' });
  const [liveResults, setLiveResults] = useState([]);
  const [finalResult, setFinalResult] = useState(null);
  const [error, setError] = useState(null);
  const [expandedResults, setExpandedResults] = useState(new Set());
  const cancelRef = useRef(false);

  const config = loadConfig() || {
    selectedModels: ['GPT4O'],
    selectedCategories: ['capability', 'safety', 'adversarial'],
    countPerCategory: 5,
    useLlmJudge: false,
    judgeModel: null,
    consistencyRuns: 1,
    customPrompts: '',
  };

  const totalTests = config.countPerCategory * config.selectedCategories.length;

  const runAllTests = useCallback(async () => {
    setRunning(true);
    setError(null);
    setFinalResult(null);
    setLiveResults([]);
    cancelRef.current = false;

    const customPrompts = config.customPrompts
      ? config.customPrompts.split('\n').map(s => s.trim()).filter(Boolean)
      : [];

    try {
      if (config.selectedModels.length >= 2) {
        // Compare mode
        setProgress({ current: 0, total: config.selectedModels.length, phase: 'Comparing models...', model: '' });

        const payload = {
          models: config.selectedModels.map(id => ({
            model_id: id,
            provider: 'abacusai',
            temperature: 0.2,
            max_tokens: 1024,
            system_message: 'You are a helpful AI assistant.',
          })),
          categories: config.selectedCategories,
          count_per_category: config.countPerCategory,
          use_llm_judge: config.useLlmJudge,
          judge_model: config.judgeModel,
        };

        let result;
        try {
          result = await compareModels(payload);
        } catch {
          // Use mock data if API fails
          result = MOCK_COMPARISON;
          toast('Using demo data (backend unavailable)', { icon: 'ℹ️' });
        }

        setFinalResult(result);
        saveToHistory({
          id: result.comparison_id || `cmp_${Date.now()}`,
          type: 'comparison',
          models: config.selectedModels,
          summary: { total_tests: totalTests * config.selectedModels.length, models_tested: config.selectedModels.length, best_model: result.rankings?.[0]?.model_id },
          categories: config.selectedCategories,
        });
        if (result.comparison_id) saveResult(result.comparison_id, result);
        toast.success('Comparison complete!');

      } else {
        // Single model test
        const modelId = config.selectedModels[0];
        setProgress({ current: 0, total: totalTests, phase: 'Running tests...', model: modelId });

        const payload = {
          model: {
            model_id: modelId,
            provider: 'abacusai',
            temperature: 0.2,
            max_tokens: 1024,
            system_message: 'You are a helpful AI assistant.',
          },
          categories: config.selectedCategories,
          count_per_category: config.countPerCategory,
          use_llm_judge: config.useLlmJudge,
          judge_model: config.judgeModel,
          consistency_runs: config.consistencyRuns,
          custom_prompts: customPrompts.length > 0 ? customPrompts : undefined,
        };

        let result;
        try {
          // Simulate progress updates
          const progressInterval = setInterval(() => {
            if (cancelRef.current) {
              clearInterval(progressInterval);
              return;
            }
            setProgress(prev => {
              const next = Math.min(prev.current + 1, prev.total);
              return { ...prev, current: next, phase: next === prev.total ? 'Finalizing...' : 'Running tests...' };
            });
          }, 800);

          result = await testModel(payload);
          clearInterval(progressInterval);
        } catch {
          result = MOCK_TEST_RESULT;
          toast('Using demo data (backend unavailable)', { icon: 'ℹ️' });
        }

        if (cancelRef.current) return;

        // Populate live results from the report
        const results = result.report?.results || [];
        setLiveResults(results);
        setProgress({ current: totalTests, total: totalTests, phase: 'Complete', model: modelId });
        setFinalResult(result);

        saveToHistory({
          id: result.report_id || `rpt_${Date.now()}`,
          type: 'test',
          model: modelId,
          summary: result.summary,
          categories: config.selectedCategories,
        });
        if (result.report_id) saveResult(result.report_id, result);
        toast.success('Tests complete!');
      }
    } catch (err) {
      setError(err.message);
      toast.error(`Test failed: ${err.message}`);
    } finally {
      setRunning(false);
    }
  }, [config, totalTests]);

  const handleCancel = () => {
    cancelRef.current = true;
    setRunning(false);
    toast('Tests cancelled');
  };

  const toggleExpanded = (id) => {
    setExpandedResults(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const progressPct = progress.total > 0 ? Math.round((progress.current / progress.total) * 100) : 0;

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-gray-900">Run Tests</h2>
          <p className="text-gray-500 mt-1">
            {config.selectedModels.length >= 2
              ? `Comparing ${config.selectedModels.length} models`
              : `Testing ${config.selectedModels[0] || 'model'}`}
            {' · '}{config.selectedCategories.join(', ')} · {totalTests} tests
          </p>
        </div>
        <div className="flex items-center gap-2">
          {!running ? (
            <button
              onClick={runAllTests}
              disabled={config.selectedModels.length === 0}
              className="btn-primary text-sm"
            >
              <Play className="w-4 h-4" /> Start Tests
            </button>
          ) : (
            <button onClick={handleCancel} className="btn-danger text-sm">
              <Square className="w-4 h-4" /> Cancel
            </button>
          )}
        </div>
      </div>

      {/* Progress Bar */}
      {(running || progress.total > 0) && (
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-3">
              {running ? (
                <Loader2 className="w-5 h-5 text-brand-600 animate-spin" />
              ) : progress.phase === 'Complete' ? (
                <CheckCircle2 className="w-5 h-5 text-emerald-600" />
              ) : (
                <Clock className="w-5 h-5 text-gray-400" />
              )}
              <div>
                <p className="text-sm font-medium text-gray-900">{progress.phase}</p>
                {progress.model && <p className="text-xs text-gray-500">Model: {progress.model}</p>}
              </div>
            </div>
            <span className="text-sm font-semibold text-gray-700">{progressPct}%</span>
          </div>
          <div className="w-full h-3 bg-gray-100 rounded-full overflow-hidden">
            <div
              className={clsx(
                'h-full rounded-full transition-all duration-500',
                progress.phase === 'Complete' ? 'bg-emerald-500' : 'bg-brand-600'
              )}
              style={{ width: `${progressPct}%` }}
            />
          </div>
          <p className="text-xs text-gray-400 mt-2">
            {progress.current} / {progress.total} tests completed
          </p>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="card bg-red-50 border-red-200">
          <div className="flex items-center gap-3">
            <AlertTriangle className="w-5 h-5 text-red-600" />
            <div>
              <p className="text-sm font-medium text-red-800">Test execution error</p>
              <p className="text-xs text-red-600 mt-1">{error}</p>
            </div>
          </div>
        </div>
      )}

      {/* Final Result Summary */}
      {finalResult && (
        <div className="card border-emerald-200 bg-emerald-50">
          <h3 className="text-lg font-semibold text-gray-900 mb-3">
            {finalResult.comparison_id ? 'Comparison Results' : 'Test Results'}
          </h3>
          {finalResult.summary && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              <div>
                <p className="text-xs text-gray-500">Total Tests</p>
                <p className="text-xl font-bold text-gray-900">{finalResult.summary.total_tests}</p>
              </div>
              <div>
                <p className="text-xs text-gray-500">Passed</p>
                <p className="text-xl font-bold text-emerald-600">{finalResult.summary.passed}</p>
              </div>
              <div>
                <p className="text-xs text-gray-500">Failed</p>
                <p className="text-xl font-bold text-red-600">{finalResult.summary.failed}</p>
              </div>
              <div>
                <p className="text-xs text-gray-500">Pass Rate</p>
                <p className="text-xl font-bold text-brand-600">{Math.round((finalResult.summary.pass_rate || 0) * 100)}%</p>
              </div>
            </div>
          )}
          {finalResult.rankings && (
            <div className="mt-4">
              <p className="text-sm font-medium text-gray-700 mb-2">Rankings:</p>
              <div className="space-y-2">
                {finalResult.rankings.map((r, i) => (
                  <div key={r.model_id} className="flex items-center gap-3 p-2 bg-white rounded-lg">
                    <span className={clsx(
                      'w-7 h-7 rounded-full flex items-center justify-center font-bold text-sm',
                      i === 0 ? 'bg-amber-100 text-amber-700' : 'bg-gray-100 text-gray-600'
                    )}>
                      {i + 1}
                    </span>
                    <span className="text-sm font-medium text-gray-900 flex-1">{r.model_id}</span>
                    <span className="text-sm font-semibold text-gray-700">{Math.round((r.score || 0) * 100)}%</span>
                  </div>
                ))}
              </div>
            </div>
          )}
          <div className="mt-4 flex gap-2">
            <button onClick={() => navigate('/results')} className="btn-primary text-sm">
              View Full Results
            </button>
            <button onClick={() => navigate('/attractors')} className="btn-secondary text-sm">
              View Attractors
            </button>
          </div>
        </div>
      )}

      {/* Live Results Table */}
      {liveResults.length > 0 && (
        <div className="card">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">
            Test Results ({liveResults.filter(r => r.passed).length}/{liveResults.length} passed)
          </h3>
          <div className="space-y-2">
            {liveResults.map((result) => (
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
                    <span className="text-xs text-gray-400 flex-shrink-0">{result.latency_ms}ms</span>
                  )}
                  {expandedResults.has(result.test_id)
                    ? <ChevronUp className="w-4 h-4 text-gray-400 flex-shrink-0" />
                    : <ChevronDown className="w-4 h-4 text-gray-400 flex-shrink-0" />
                  }
                </button>
                {expandedResults.has(result.test_id) && (
                  <div className="px-3 pb-3 pt-1 border-t border-gray-100 bg-gray-50">
                    <div className="grid grid-cols-2 gap-4 text-sm">
                      <div>
                        <p className="text-xs text-gray-500 mb-1">Scores</p>
                        {result.scores && Object.entries(result.scores).map(([k, v]) => (
                          <div key={k} className="flex justify-between text-xs">
                            <span className="text-gray-600">{k}</span>
                            <span className={clsx('font-medium', v >= 0.7 ? 'text-emerald-600' : 'text-red-600')}>
                              {typeof v === 'number' ? v.toFixed(2) : v}
                            </span>
                          </div>
                        ))}
                      </div>
                      {result.failures && result.failures.length > 0 && (
                        <div>
                          <p className="text-xs text-gray-500 mb-1">Failures</p>
                          {result.failures.map((f, i) => (
                            <p key={i} className="text-xs text-red-600">{f}</p>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Empty state */}
      {!running && !finalResult && liveResults.length === 0 && (
        <div className="card text-center py-16">
          <Play className="w-12 h-12 text-gray-300 mx-auto mb-4" />
          <h3 className="text-lg font-semibold text-gray-900">Ready to Run</h3>
          <p className="text-sm text-gray-500 mt-2 max-w-md mx-auto">
            Click "Start Tests" to begin testing {config.selectedModels.join(', ')} with{' '}
            {config.selectedCategories.join(', ')} test suites.
          </p>
          <button onClick={runAllTests} className="btn-primary text-sm mt-6">
            <Play className="w-4 h-4" /> Start Tests
          </button>
        </div>
      )}
    </div>
  );
}
