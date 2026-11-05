import type { ReactNode } from 'react';

export function Status({ tone = 'neutral', children }: { tone?: string; children: ReactNode }) {
  return <span className={`status-pill ${tone}`}><span className="status-dot" />{children}</span>;
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <div className="empty-state">{children}</div>;
}
