import React from 'react';
import { Loader2 } from 'lucide-react';
import clsx from 'clsx';

export default function LoadingSpinner({ size = 'md', text, className }) {
  const sizes = { sm: 'w-4 h-4', md: 'w-6 h-6', lg: 'w-10 h-10' };
  return (
    <div className={clsx('flex flex-col items-center justify-center gap-3 py-8', className)}>
      <Loader2 className={clsx('animate-spin text-brand-600', sizes[size])} />
      {text && <p className="text-sm text-gray-500">{text}</p>}
    </div>
  );
}
