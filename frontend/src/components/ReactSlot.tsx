import { useSyncExternalStore, type ReactNode } from 'react';
import { flushSync } from 'react-dom';
import { createStore } from '../lib/store';

const slots = new Map<string, ReturnType<typeof createStore<ReactNode>>>();
function slot(name: string) {
  if (!slots.has(name)) slots.set(name, createStore<ReactNode>(null));
  return slots.get(name)!;
}

/** The controller owns requests; React exclusively owns this slot's descendants. */
export function publishSlot(name: string, node: ReactNode) {
  flushSync(() => slot(name).set(node));
}

export function ReactSlot({ name, children }: { name: string; children?: ReactNode }) {
  const store = slot(name);
  const node = useSyncExternalStore(store.subscribe, store.getSnapshot);
  return node ?? children;
}

export function clearSlots() {
  slots.forEach(store => store.set(null));
}
