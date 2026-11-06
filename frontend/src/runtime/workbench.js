import { state } from './state.js';
import { listen } from '../lib/resources';
import { $, preferredScrollBehavior } from './format.js';
import { setSidebarVisible, setInspectorVisible, setPanelWidth, panelWidth } from './navigation.js';

// Focus containment is enabled only while a narrow-screen overlay is open.
export function bindWorkbenchInteractions() {
  listen($("sidebar-backdrop"), "click", () => { setSidebarVisible(false); $("toggle-sidebar-btn").focus(); });
  listen(document, "keydown", event => {
    if (document.querySelector("dialog[open]")) return;
    const panel = document.body.classList.contains('sidebar-drawer-open') ? $('primary-sidebar')
      : document.body.classList.contains('inspector-drawer-open') ? $('inspector-panel') : null;
    if (!panel) return;
    if (event.key === 'Escape' && panel.id === 'primary-sidebar') {
      event.preventDefault(); setSidebarVisible(false); $('toggle-sidebar-btn').focus(); return;
    }
    if (event.key !== 'Tab') return;
    const focusable = [...panel.querySelectorAll('button:not(:disabled), a[href], select, input, textarea, [tabindex="0"]')].filter(node => node.getClientRects().length);
    const first = focusable[0], last = focusable.at(-1);
    if (!first) return;
    if (!panel.contains(document.activeElement) || (event.shiftKey ? document.activeElement === first : document.activeElement === last)) {
      event.preventDefault(); (event.shiftKey ? last : first).focus();
    }
  });
  let wasWide = window.innerWidth >= 1200;
  let wasNarrow = window.innerWidth < 900;
  let desktopSidebar = !document.body.classList.contains("sidebar-hidden");
  listen(window, 'resize', () => {
    const narrow = window.innerWidth < 900;
    if (narrow && !wasNarrow) { desktopSidebar = !document.body.classList.contains("sidebar-hidden"); setSidebarVisible(false); }
    if (!narrow && wasNarrow) setSidebarVisible(desktopSidebar);
    wasNarrow = narrow;
    const wide = window.innerWidth >= 1200;
    if (wide !== wasWide) setInspectorVisible(wide && state.inspectorPreferred, { persist: false });
    wasWide = wide;
    setPanelWidth('inspector', panelWidth('inspector'));
    if (window.innerWidth < 900 && !document.body.classList.contains('sidebar-drawer-open')) setSidebarVisible(false);
  });
  // All incumbent tab groups receive the same arrow-key behavior.
  document.querySelectorAll('[role="tablist"]').forEach(list => {
    if (list.getAttribute('aria-label') === '执行详情') return;
    listen(list, 'keydown', event => {
      const tabs = [...list.querySelectorAll('[role="tab"]')];
      const index = tabs.indexOf(event.target);
      if (index < 0) return;
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : event.key === 'ArrowRight' ? (index + 1) % tabs.length : event.key === 'ArrowLeft' ? (index - 1 + tabs.length) % tabs.length : -1;
      if (next < 0) return;
      event.preventDefault(); tabs[next].click(); tabs[next].focus();
    });
  });
}
