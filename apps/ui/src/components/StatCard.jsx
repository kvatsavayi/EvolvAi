import React from 'react';
import clsx from 'clsx';

export default function StatCard({ icon: Icon, label, value, subtext, trend, color = 'brand' }) {
  const colorMap = {
    brand: 'bg-brand-50 text-brand-600',
    green: 'bg-emerald-50 text-emerald-600',
    red: 'bg-red-50 text-red-600',
    yellow: 'bg-amber-50 text-amber-600',
    blue: 'bg-blue-50 text-blue-600',
    purple: 'bg-purple-50 text-purple-600',
  };

  return (
    <div className="card-hover">
      <div className="flex items-start justify-between">
        <div className={clsx('p-2.5 rounded-lg', colorMap[color])}>
          <Icon className="w-5 h-5" />
        </div>
        {trend && (
          <span className={clsx(
            'text-xs font-medium',
            trend > 0 ? 'text-emerald-600' : trend < 0 ? 'text-red-600' : 'text-gray-500'
          )}>
            {trend > 0 ? '↑' : trend < 0 ? '↓' : '—'} {Math.abs(trend)}%
          </span>
        )}
      </div>
      <div className="mt-4">
        <p className="text-2xl font-bold text-gray-900">{value}</p>
        <p className="text-sm text-gray-500 mt-1">{label}</p>
        {subtext && <p className="text-xs text-gray-400 mt-0.5">{subtext}</p>}
      </div>
    </div>
  );
}
