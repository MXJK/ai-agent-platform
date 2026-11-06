import { useState, useSyncExternalStore } from 'react';
import { inspector, inspectorSelection } from '../lib/inspector';
import { humanizeStatus } from '../runtime/feedback.js';
import { auditEventTitle } from '../runtime/audit.js';
import { changeSetFileStats, chatContentForRun, handleInlineChangeAction } from '../runtime/checkpoints.js';
import { openCheckpointHistory } from '../runtime/runs.js';
import { switchView } from '../runtime/navigation.js';

const tabs = [['changes', '变更'], ['trace', 'Trace'], ['context', '上下文']];
export function RunInspector() {
  const data = useSyncExternalStore(inspector.subscribe, inspector.getSnapshot);
  const selection = useSyncExternalStore(inspectorSelection.subscribe, inspectorSelection.getSnapshot);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const files = data.changes ? changeSetFileStats(data.changes) : [];
  const path = files.some((file: any) => file.path === selection.path) ? selection.path : files[0]?.path;
  const patch = String(data.changes?.patch || '');
  // Unified diffs can lack diff --git headers; use +++/--- boundaries as fallback.
  const blocks = patch.split(/(?=^diff --git )/m);
  let visiblePatch = blocks.length > 1 ? blocks.filter(block => block.includes(`b/${path}\n`) || block.includes(`a/${path}\n`)).join('') : patch;
  if (blocks.length === 1 && files.length > 1) visiblePatch = patch.split(/(?=^--- )/m).filter(block => block.includes(`b/${path}\n`) || block.startsWith(`--- a/${path}\n`)).join('');
  const events = data.body?.stream_events?.length ? data.body.stream_events : (data.body?.trace?.length ? data.body.trace : data.body?.result?.trace || []);
  const change = data.changes;
  const live = change && ['direct', 'worktree'].includes(change.apply_mode) && change.status === 'applied' && files.length > 0 && files.every((file: any) => Object.hasOwn(change.validation_summary?.post_write_file_hashes || {}, file.path));
  async function act(action: string) {
    const content = chatContentForRun(data.run);
    const card = content?.closest(".chat-bubble")?.querySelector(".inline-change-review");
    if (!content || !change || !card) return;
    setBusy(true); setError('');
    try { await handleInlineChangeAction(content, change, action, card); }
    catch (err) { setError(String(err)); }
    finally { setBusy(false); }
  }
  return <div className="run-inspector-content">
    <div role="tablist" aria-label="执行详情" className="workbench-tabs">{tabs.map(([id, label], index) => <button key={id} id={`inspector-tab-${id}`} role="tab" type="button" aria-selected={selection.tab === id} aria-controls={`inspector-${id}`} tabIndex={selection.tab === id ? 0 : -1} onClick={() => inspectorSelection.set({ ...selection, tab: id })} onKeyDown={event => {
      let next = index;
      if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
      else if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = tabs.length - 1;
      else return;
      event.preventDefault(); inspectorSelection.set({ ...selection, tab: tabs[next][0] }); document.getElementById(`inspector-tab-${tabs[next][0]}`)?.focus();
    }}>{label}</button>)}</div>
    <div className="inspector-binding" title={data.run || data.session}>{data.run ? `Run ${data.run}` : '当前会话'}</div>
    <section id="inspector-changes" role="tabpanel" aria-labelledby="inspector-tab-changes" hidden={selection.tab !== 'changes'}>
      {!data.run ? <p className="inspector-empty">开始任务后显示执行详情</p> : !change ? <p className="inspector-empty">当前 Run 暂无变更记录。</p> : <>
        <div className="inspector-file-list">{files.map((file: any) => <button key={file.path} type="button" className={path === file.path ? 'selected' : ''} title={file.path} onClick={() => inspectorSelection.set({ ...selection, path: file.path })}><code>{file.path}</code><span className="diff-addition">+{file.additions}</span><span className="diff-deletion">−{file.deletions}</span></button>)}</div>
        <div className="inspector-change-status"><strong>{humanizeStatus(change.status)}</strong><span>{change.apply_mode}</span></div>
        <p className="inspector-note">{change.apply_mode === 'patch_only' ? '仅生成补丁，未写入真实工作区。' : live ? change.apply_mode === 'worktree' ? '变更已写入隔离 worktree，可校验后回滚。' : '变更已写入注册工作区，可校验后回滚。' : '操作前由服务端校验补丁与文件基线。'}</p>
        {change.worktree_path && <p className="inspector-note"><code>{change.worktree_path}</code></p>}
        <pre className="inspector-diff" aria-label={`${path || '当前 Run'} Diff`} tabIndex={0}><code>{(visiblePatch || '没有可显示的补丁').split('\n').map((line, i) => <span key={i} className={line.startsWith('+') ? 'diff-added' : line.startsWith('-') ? 'diff-removed' : ''}>{line || ' '}</span>)}</code></pre>
        {change.error && <p role="alert">{change.error}</p>}
        <div className="inspector-change-actions" aria-busy={busy}>{live ? <button type="button" className="button secondary" disabled={busy} onClick={() => void act('revert')}>回滚已写入变更</button> : change.status === 'ready' && <><button type="button" className="button secondary" disabled={busy} onClick={() => void act('reject')}>拒绝变更</button><button type="button" className="button primary" disabled={busy || change.apply_mode === 'patch_only'} onClick={() => void act('apply')}>应用旧版 ChangeSet</button></>}</div>
        {error && <p role="alert">{error}</p>}
      </>}
      {data.run && <button className="button ghost inspector-checkpoints" type="button" onClick={() => void openCheckpointHistory(data.run)}>查看检查点</button>}
    </section>
    <section id="inspector-trace" role="tabpanel" aria-labelledby="inspector-tab-trace" hidden={selection.tab !== 'trace'}>
      {!data.run ? <p className="inspector-empty">开始任务后显示执行详情</p> : <><p className="inspector-note">当前运行最近 100 条事件；完整记录见审计。</p><ol className="inspector-events">{events.slice(-100).map((event: any, index: number) => <li key={`${data.run}-${event.id || index}`}><details><summary><span>{auditEventTitle(event)}</span><small>{event.type || event.status || ''}</small></summary><pre>{JSON.stringify(event, null, 2)}</pre></details></li>)}</ol>{!events.length && <p className="inspector-empty">等待执行事件。</p>}</>}
      <button className="button ghost" type="button" onClick={() => switchView('trace-audit')}>打开完整审计</button>
    </section>
    <section id="inspector-context" role="tabpanel" aria-labelledby="inspector-tab-context" hidden={selection.tab !== 'context'}><dl className="inspector-context"><div><dt>工作区</dt><dd>{data.workspace || '未选择工作区'}</dd></div><div><dt>模型 / 策略</dt><dd>{data.model || '默认配置'}</dd></div><div><dt>权限模式</dt><dd>{data.permission || '逐次确认'}</dd></div><div><dt>上下文用量</dt><dd>{data.budget || '暂无数据'}</dd></div></dl><p className="inspector-note">配置不等于实际 Prompt。用量沿用服务端计数与已有估算；未提供已注入文件清单。</p></section>
  </div>;
}
