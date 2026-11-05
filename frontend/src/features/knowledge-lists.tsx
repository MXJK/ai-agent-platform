import { publishSlot } from '../components/ReactSlot';
import { EmptyState } from '../components/Status';
import { formatDate } from '../runtime/format.js';
import { truncate } from '../runtime/feedback.js';
import { documentIndexPresentation, formatByteSize } from '../runtime/knowledge.js';

export type KnowledgeBase = { id: string; name: string; document_count: number; description?: string; tags?: string[] };
export type KnowledgeDocument = { id: string; title: string; filename: string; tags?: string[]; last_index_status: string; is_searchable: boolean; byte_size: number; chunk_count: number; updated_at: string };

export function KnowledgeList({ items, selectedId, hasItems }: { items: KnowledgeBase[]; selectedId: string; hasItems: boolean }) {
  if (!items.length) return <EmptyState>{hasItems ? '没有匹配的知识库' : '暂无知识库，可以从新建开始。'}</EmptyState>;
  return items.map(item => <button key={item.id} className="knowledge-base-row" type="button" role="option" aria-selected={item.id === selectedId} data-knowledge-base-id={item.id}>
    <strong>{item.name}</strong><span className="kb-count">{item.document_count}</span><small>{item.id} · {truncate((item.tags || []).join(' / ') || item.description || '暂无标签', 48)}</small>
  </button>);
}

type DocumentProps = { documents: KnowledgeDocument[]; selected: Set<string>; loading: boolean; hasKnowledgeBase: boolean; onSelect: (id: string, selected: boolean) => void };
export function DocumentRows({ documents, selected, loading, hasKnowledgeBase, onSelect }: DocumentProps) {
  if (loading || !hasKnowledgeBase || !documents.length) return <tr><td colSpan={6}><div className="empty-state" aria-busy={loading}>{loading ? '正在加载文档…' : !hasKnowledgeBase ? '请先选择知识库' : '这个知识库还没有文档。使用“批量上传”建立第一份索引。'}</div></td></tr>;
  return documents.map(item => {
    const status = documentIndexPresentation(item.last_index_status, item.is_searchable);
    const extension = item.filename.includes('.') ? item.filename.split('.').pop()?.toUpperCase() : 'FILE';
    return <tr key={item.id} className="document-row" data-document-id={item.id} data-index-state={status.state}>
      <td><input className="document-select" type="checkbox" aria-label={`选择 ${item.title}`} checked={selected.has(item.id)} onChange={event => onSelect(item.id, event.target.checked)} /></td>
      <td className="document-name-cell"><button className="document-open-button" type="button" data-open-document={item.id}><strong>{item.title}</strong><span>{item.filename}</span></button>{Boolean(item.tags?.length) && <small className="document-tag-summary">{item.tags!.join(' / ')}</small>}</td>
      <td data-label="状态"><span className={`index-status ${status.className}`}><i aria-hidden="true" />{status.label}</span></td>
      <td data-label="类型"><span className="document-type">{extension} · {formatByteSize(item.byte_size)}</span></td>
      <td data-label="分块"><span className="document-chunk-count">{item.chunk_count}</span></td>
      <td data-label="更新"><span className="document-date">{formatDate(item.updated_at)}</span></td>
    </tr>;
  });
}

export const renderKnowledgeList = (items: KnowledgeBase[], selectedId: string, hasItems: boolean) => publishSlot('knowledge-bases', <KnowledgeList items={items} selectedId={selectedId} hasItems={hasItems} />);
export const renderDocumentRows = (props: DocumentProps) => publishSlot('documents', <DocumentRows {...props} />);
