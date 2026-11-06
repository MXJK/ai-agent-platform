import type { ReactNode } from 'react';
import { useState } from 'react';
/** Keep domain islands mounted so switching tabs cannot discard form drafts. */
export function SectionTabs({ id, tabs }: { id: string; tabs: { label: string; content: ReactNode }[] }) {
  const [selected, setSelected] = useState(0);
  return <><div className="workbench-tabs" role="tablist" aria-label={id === 'models' ? '模型管理' : '工具类型'}>{tabs.map((tab, index) => <button key={tab.label} type="button" role="tab" id={`${id}-tab-${index}`} aria-controls={`${id}-panel-${index}`} aria-selected={selected === index} tabIndex={selected === index ? 0 : -1} onClick={() => setSelected(index)} onKeyDown={event => {
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : event.key === 'ArrowRight' ? (index + 1) % tabs.length : event.key === 'ArrowLeft' ? (index + tabs.length - 1) % tabs.length : -1;
    if (next < 0) return;
    event.preventDefault(); event.stopPropagation(); setSelected(next); document.getElementById(`${id}-tab-${next}`)?.focus();
  }}>{tab.label}</button>)}</div>{tabs.map((tab, index) => <section role="tabpanel" key={tab.label} id={`${id}-panel-${index}`} aria-labelledby={`${id}-tab-${index}`} hidden={selected !== index}>{tab.content}</section>)}</>;
}
