/** Small external store: snapshots are stable until a value actually changes. */
export function createStore<T>(initial: T) {
  let snapshot = initial;
  const listeners = new Set<() => void>();
  return {
    getSnapshot: () => snapshot,
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
    set(next: T) {
      if (Object.is(snapshot, next)) return;
      snapshot = next;
      listeners.forEach(listener => listener());
    },
  };
}
