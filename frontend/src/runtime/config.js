

export const runtimeRefs = { composerDraftSaveTimer: null, conversationFollowFrame: null, modelRegistryRefreshTimer: null, contextRingTooltipNode: null };
export const API_BASE = "/api/v1";
export const UI_STORAGE_KEY = "ai-agent-platform-ui-v2";
export const NATIVE_PICKER_LOCAL_ONLY_DETAIL =
  "native directory picker is only available for local mode";
export const FINAL_RUN_STATUSES = new Set(["completed", "partial", "blocked", "cancelled", "failed"]);
export const SUSPENDED_RUN_STATUSES = new Set(["waiting_approval", "waiting_input", "paused"]);
export const TERMINAL_RUN_STATUSES = new Set([...FINAL_RUN_STATUSES, ...SUSPENDED_RUN_STATUSES]);
export const TRACE_STEP_REVEAL_DELAY_MS = 16;
export const MAX_TRACE_REPLAY_MS = 1200;
export const PANEL_WIDTHS = {
  sidebar: { cssVariable: "--sidebar-width", min: 188, max: 260, defaultValue: 208 },
  inspector: { cssVariable: "--inspector-width", min: 320, max: 480, defaultValue: 384 },
};
export const responseTimers = new WeakMap();
