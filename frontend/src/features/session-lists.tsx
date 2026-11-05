import { publishSlot } from '../components/ReactSlot';
import { EmptyState } from '../components/Status';
import { formatDate } from '../runtime/format.js';
import { recentSessionGroup } from '../runtime/sessions.js';

export type Session = { id: string; title?: string; updated_at: string; message_count: number; last_message_preview?: string; archived_at?: string | null };
type Props = { sessions: Session[]; selectedId: string };

export function SessionList({ sessions, selectedId }: Props) {
  if (!sessions.length) return <EmptyState>暂无会话，创建一个会话开始工作。</EmptyState>;
  return sessions.map(session => <article key={session.id} className={`session-item ${session.id === selectedId ? 'active' : ''}`} data-session-id={session.id}>
    <button className="session-open" type="button" data-session-action="open"><strong>{session.title || '新会话'}</strong><span>{formatDate(session.updated_at)} · {session.message_count} 条消息</span><span className="session-message-preview">{session.last_message_preview || '暂无消息'}</span></button>
    <div className="session-actions"><button className="text-button" type="button" data-session-action="rename">重命名</button><button className="text-button" type="button" data-session-action={session.archived_at ? 'restore' : 'archive'}>{session.archived_at ? '恢复' : '归档'}</button></div>
  </article>);
}

export function RecentSessions({ sessions, selectedId }: Props) {
  if (!sessions.length) return <div className="empty-state compact">暂无会话</div>;
  const groups = new Map<string, Session[]>();
  for (const session of sessions) {
    const label = recentSessionGroup(session.updated_at);
    groups.set(label, [...(groups.get(label) || []), session]);
  }
  return [...groups].map(([label, items]) => <section key={label} className="recent-session-group"><h3>{label}</h3>{items.map(session => <button key={session.id} type="button" className={`recent-session-item ${session.id === selectedId ? 'active' : ''}`} data-session-id={session.id}><strong>{session.title || '新会话'}</strong><small>{formatDate(session.updated_at)}</small></button>)}</section>);
}

export const renderSessionCards = (sessions: Session[], selectedId: string) => publishSlot('sessions', <SessionList sessions={sessions} selectedId={selectedId} />);
export const renderRecentSessionCards = (sessions: Session[], selectedId: string) => publishSlot('recent-sessions', <RecentSessions sessions={sessions} selectedId={selectedId} />);
