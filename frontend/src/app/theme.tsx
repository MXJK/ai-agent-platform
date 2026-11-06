import { useState } from 'react';
import { listen } from '../lib/resources';
export type Theme = 'system' | 'dark' | 'light';
const key = 'cogent.appearance';
export function readTheme(): Theme {
  try { const value = localStorage.getItem(key); return value === 'dark' || value === 'light' ? value : 'system'; }
  catch { return 'system'; }
}
export function applyTheme(theme: Theme) {
  const dark = theme === 'dark' || (theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  document.documentElement.style.colorScheme = dark ? 'dark' : 'light';
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', dark ? '#19191c' : '#fcfcfd');
}
let preference = readTheme();
export function bindTheme() {
  applyTheme(preference);
  listen(window.matchMedia('(prefers-color-scheme: dark)'), 'change', () => {
    if (preference === 'system') applyTheme(preference);
  });
}
export function ThemeControl() {
  const [value, setValue] = useState(preference);
  return <label className="theme-control"><span>外观</span><select aria-label="外观主题" value={value} onChange={event => {
    preference = event.target.value as Theme;
    setValue(preference); applyTheme(preference);
    try { localStorage.setItem(key, preference); } catch { /* Optional local preference. */ }
  }}><option value="system">跟随系统</option><option value="dark">深色</option><option value="light">浅色</option></select></label>;
}
