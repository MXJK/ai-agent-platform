import { init } from '../runtime/bootstrap.js';
import { apiClient } from '../runtime/http.js';
import { state } from '../runtime/state.js';
import { disposeResources } from '../lib/resources';

export function startWorkbench(container: HTMLElement) {
  let disposed = false;
  let started = false;
  let observer: MutationObserver | undefined;
  const start = () => {
    if (disposed || started || !container.querySelector('#main-workspace')) return;
    started = true;
    observer?.disconnect();
    void init().catch((error: unknown) => {
      if (disposed) return;
      const region = document.getElementById('toast-region');
      if (region) region.textContent = `工作台加载失败：${error instanceof Error ? error.message : String(error)}`;
    });
  };
  observer = new MutationObserver(start);
  observer.observe(container, { childList: true, subtree: true });
  start();
  return () => {
    disposed = true;
    observer?.disconnect();
    state.chatController?.abort();
    state.ragRequestController?.abort();
    state.documentRequestController?.abort();
    apiClient.abortAll();
    disposeResources();
  };
}
