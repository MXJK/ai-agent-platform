let listenerScope: AbortController | null = null;
const timeouts = new Set<number>();
const intervals = new Set<number>();
const frames = new Set<number>();

export function listen(target: EventTarget, type: string, listener: EventListenerOrEventListenerObject, options?: AddEventListenerOptions | boolean) {
  listenerScope ??= new AbortController();
  const settings = typeof options === 'boolean' ? { capture: options } : options || {};
  const signal = settings.signal
    ? AbortSignal.any([settings.signal, listenerScope.signal])
    : listenerScope.signal;
  target.addEventListener(type, listener, { ...settings, signal });
}

export function managedSetTimeout(callback: () => void, delay?: number) {
  const id = window.setTimeout(() => { timeouts.delete(id); callback(); }, delay);
  timeouts.add(id);
  return id;
}

export function managedSetInterval(callback: () => void, delay?: number) {
  const id = window.setInterval(callback, delay);
  intervals.add(id);
  return id;
}

export function managedRequestAnimationFrame(callback: FrameRequestCallback) {
  const id = window.requestAnimationFrame(time => { frames.delete(id); callback(time); });
  frames.add(id);
  return id;
}

export function disposeResources() {
  listenerScope?.abort();
  listenerScope = null;
  timeouts.forEach(id => window.clearTimeout(id));
  intervals.forEach(id => window.clearInterval(id));
  frames.forEach(id => window.cancelAnimationFrame(id));
  timeouts.clear(); intervals.clear(); frames.clear();
}
