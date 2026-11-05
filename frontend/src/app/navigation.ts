import { createStore } from '../lib/store';

export const views = ['chat', 'rag', 'memory', 'models', 'tools', 'sessions', 'evals', 'trace-audit', 'activity'] as const;
export type View = typeof views[number];
export const navigation = createStore<View>('chat');
export function isView(value: string): value is View {
  return views.some(view => view === value);
}
