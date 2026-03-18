import React from 'react';
import clsx from 'clsx';

export function StatusBadge({ status }) {
  const styles = {
    online: 'badge-green',
    offline: 'badge-red',
    running: 'badge-yellow',
    completed: 'badge-green',
    failed: 'badge-red',
    idle: 'badge-gray',
    pass: 'badge-green',
    fail: 'badge-red',
  };
  return <span className={clsx(styles[status] || 'badge-gray')}>{status}</span>;
}

export function SeverityBadge({ severity }) {
  const styles = {
    critical: 'badge-red',
    high: 'badge-red',
    medium: 'badge-yellow',
    low: 'badge-blue',
    info: 'badge-gray',
  };
  return <span className={clsx(styles[severity] || 'badge-gray')}>{severity}</span>;
}

export function CategoryBadge({ category }) {
  const styles = {
    capability: 'badge-blue',
    safety: 'badge-green',
    adversarial: 'badge-red',
    regression: 'badge-purple',
    custom: 'badge-gray',
  };
  return <span className={clsx(styles[category] || 'badge-gray')}>{category}</span>;
}
