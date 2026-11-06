import type { ReactNode } from 'react';
import { useRef } from 'react';
/** Closing hides the editor; its draft and controller-owned nodes remain mounted. */
export function EditorDialog({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null);
  return <div className="editor-launcher"><button type="button" className="button secondary" onClick={() => dialog.current?.showModal()}>{title}</button><dialog ref={dialog} id={id} className="resource-editor-dialog" aria-labelledby={`${id}-title`}><header className="dialog-header"><h2 id={`${id}-title`}>{title}</h2><button type="button" className="button ghost" onClick={() => dialog.current?.close()}>关闭</button></header><div className="resource-editor-body">{children}</div><p className="editor-draft-note">关闭后保留当前草稿；保存后才会更新配置。</p></dialog></div>;
}
