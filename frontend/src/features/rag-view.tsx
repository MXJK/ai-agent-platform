import { memo } from 'react';
import { ReactSlot } from '../components/ReactSlot';
import { ViewPanel } from '../components/ViewPanel';

export const KnowledgeView = memo(function KnowledgeView() {
 return (<ViewPanel id="rag-view" aria-labelledby="rag-title" view="rag" className="view-panel">
          <header className="view-header compact">
            <div >
              <p className="eyebrow">{"Grounded Knowledge"}</p>
              <h1 id="rag-title">{"知识库"}</h1>
              <p >{"选中一个知识库，集中管理文档、索引健康和检索问答。"}</p>
            </div>
            <span id="rag-status" className="meta-badge" role="status" aria-live="polite">{"等待加载"}</span>
          </header>

          <div className="knowledge-workbench">
            <aside className="surface knowledge-catalog" aria-label="知识库目录">
              <div className="knowledge-catalog-heading">
                <div ><span className="utility-label">{"Knowledge bases"}</span><h2 >{"知识库"}</h2></div>
                <button id="new-knowledge-base-btn" className="icon-button compact-icon" type="button" aria-label="新建知识库" title="新建知识库">
                  <svg className="app-icon" aria-hidden="true"><use href="#icon-plus"></use></svg>
                </button>
              </div>
              <label className="mobile-kb-selector" htmlFor="mobile-knowledge-base-select">
                <span >{"当前知识库"}</span>
                <select id="mobile-knowledge-base-select" aria-label="选择当前知识库"></select>
              </label>
              <label className="catalog-search" htmlFor="knowledge-base-search-input">
                <span className="sr-only">{"搜索知识库"}</span>
                <input id="knowledge-base-search-input" type="search" placeholder="搜索名称、ID 或标签" />
              </label>
              <div id="knowledge-base-list" className="knowledge-base-list" role="listbox" aria-label="选择知识库"><ReactSlot name="knowledge-bases" /></div>
              <button id="refresh-knowledge-bases-btn" className="text-button catalog-refresh" type="button">{"刷新目录"}</button>
            </aside>

            <section className="knowledge-detail" aria-label="当前知识库">
              <header className="surface knowledge-context-header">
                <input id="kb-id-input" type="hidden" />
                <div className="knowledge-context-copy">
                  <span className="utility-label">{"当前知识库"}</span>
                  <h2 id="selected-kb-name">{"请选择知识库"}</h2>
                  <p id="selected-kb-description">{"选中后即可查看和管理文档。"}</p>
                  <div id="selected-kb-tags" className="tag-row"></div>
                </div>
                <div className="knowledge-health-summary" aria-live="polite">
                  <span id="selected-kb-health" className="index-status neutral"><i aria-hidden="true"></i>{"未选择"}</span>
                  <strong id="selected-kb-document-count">{"—"}</strong>
                  <small >{"个文档"}</small>
                </div>
              </header>

              <div className="knowledge-tabs" role="tablist" aria-label="知识库功能">
                <button id="knowledge-documents-tab" className="knowledge-tab active" type="button" role="tab" aria-selected="true" aria-controls="knowledge-documents-panel" data-rag-tab="documents">{"文档"}</button>
                <button id="knowledge-ask-tab" className="knowledge-tab" type="button" role="tab" aria-selected="false" aria-controls="knowledge-ask-panel" data-rag-tab="ask">{"检索问答"}</button>
                <button id="knowledge-settings-tab" className="knowledge-tab" type="button" role="tab" aria-selected="false" aria-controls="knowledge-settings-panel" data-rag-tab="settings">{"设置"}</button>
              </div>

              <section id="knowledge-documents-panel" className="surface knowledge-tab-panel" role="tabpanel" aria-labelledby="knowledge-documents-tab" data-rag-tab-panel="documents">
                <div className="document-toolbar">
                  <div className="document-filters">
                    <label htmlFor="document-search-input"><span className="sr-only">{"搜索文档"}</span><input id="document-search-input" type="search" placeholder="搜索标题或文件名" /></label>
                    <label htmlFor="document-status-filter"><span className="sr-only">{"索引状态"}</span><select id="document-status-filter"><option value="">{"全部状态"}</option><option value="active">{"可用"}</option><option value="failed">{"失败"}</option><option value="embedding">{"嵌入中"}</option><option value="vector_written">{"写入中"}</option></select></label>
                    <label htmlFor="document-sort-input"><span className="sr-only">{"文档排序"}</span><select id="document-sort-input"><option value="updated_at_desc">{"最近更新"}</option><option value="created_at_desc">{"最近创建"}</option><option value="title_asc">{"标题 A–Z"}</option></select></label>
                  </div>
                  <div className="button-row document-actions" aria-label="文档批量操作">
                    <button id="bulk-delete-documents-btn" className="button ghost" type="button" disabled={true}>{"删除已选"}</button>
                    <label className="button primary upload-trigger" htmlFor="document-files-input">{"批量上传"}</label>
                    <input id="document-files-input" className="sr-only" type="file" multiple={true} accept=".pdf,.docx,.md,.markdown,.txt,.html,.json,.yaml,.yml,.toml,.py,.js,.jsx,.ts,.tsx,.css,.go,.java,.rs" />
                  </div>
                </div>
                <div id="selected-document-files" className="upload-queue" hidden={true}></div>
                <div id="document-upload-actions" className="upload-actions" hidden={true}>
                  <span >{"单个文件最大 20 MiB；同名文件需要显式确认替换。"}</span>
                  <button id="ingest-doc-btn" className="button secondary" type="button">{"开始上传"}</button>
                </div>
                <div className="document-table-wrap">
                  <table className="document-table">
                    <thead ><tr ><th ><input id="select-all-documents" type="checkbox" aria-label="选择当前页所有文档" /></th><th >{"文档"}</th><th >{"索引状态"}</th><th >{"类型 / 大小"}</th><th >{"分块"}</th><th >{"更新时间"}</th></tr></thead>
                    <tbody id="knowledge-document-rows"><ReactSlot name="documents" /></tbody>
                  </table>
                </div>
                <footer className="document-pagination">
                  <span id="document-page-summary">{"—"}</span>
                  <div className="button-row"><button id="previous-document-page" className="button ghost" type="button">{"上一页"}</button><button id="next-document-page" className="button ghost" type="button">{"下一页"}</button></div>
                </footer>
              </section>

              <section id="knowledge-ask-panel" className="surface knowledge-tab-panel" role="tabpanel" aria-labelledby="knowledge-ask-tab" data-rag-tab-panel="ask" hidden={true}>
                <div className="surface-heading"><div ><span className="utility-label">{"Retrieval"}</span><h2 >{"检索并生成回答"}</h2></div></div>
              <label htmlFor="rag-question-input">{"问题或搜索词"}</label>
              <textarea id="rag-question-input" rows={6} maxLength={4000} defaultValue={"这个项目主要包含哪些后端能力？"} />
              <details className="advanced-options">
                <summary >
                  <span >{"检索参数"}</span>
                  <span id="rag-strategy-summary" className="strategy-pill" data-strategy="rrf">{"策略：检测中"}</span>
                </summary>
                <div className="field-grid">
                  <label htmlFor="rag-limit-input">{"最终结果数"}<input id="rag-limit-input" type="number" min="1" max="20" defaultValue="5" /></label>
                  <label htmlFor="rag-recall-limit-input">{"召回候选数"}<input id="rag-recall-limit-input" type="number" min="1" max="100" defaultValue="20" /></label>
                </div>
                <div className="rerank-control">
                  <div >
                    <strong >{"CrossEncoder 精排"}</strong>
                    <span id="rag-rerank-description">{"正在检查服务端精排能力…"}</span>
                  </div>
                  <button id="rag-rerank-toggle" className="button rerank-toggle" type="button" aria-pressed="false" disabled={true}>{"精排：关闭"}</button>
                </div>
              </details>
              <div className="button-row rag-actions">
                <button id="search-rag-btn" className="button secondary" type="button">{"仅检索"}</button>
                <button id="ask-rag-btn" className="button primary" type="button">{"生成回答"}</button>
              </div>
              <div id="rag-answer" className="rich-output empty-output">{"检索结果和回答会显示在这里。"}</div>
              <div id="rag-citations" className="citation-list"></div>
              </section>

              <section id="knowledge-settings-panel" className="surface knowledge-tab-panel" role="tabpanel" aria-labelledby="knowledge-settings-tab" data-rag-tab-panel="settings" hidden={true}>
                <div className="surface-heading"><div ><span className="utility-label">{"Catalog metadata"}</span><h2 id="knowledge-settings-title">{"知识库设置"}</h2></div></div>
                <div className="field-grid">
                  <label htmlFor="kb-catalog-id-input">{"知识库 ID"}<input id="kb-catalog-id-input" maxLength={128} placeholder="例如 product_docs" /></label>
                  <label htmlFor="kb-name-input">{"名称"}<input id="kb-name-input" maxLength={128} placeholder="例如 产品文档" /></label>
                </div>
                <label htmlFor="kb-description-input">{"描述"}</label>
                <textarea id="kb-description-input" rows={4} maxLength={1000} placeholder="说明这个知识库适合回答哪些问题"></textarea>
                <label htmlFor="kb-tags-input">{"标签"}</label>
                <input id="kb-tags-input" maxLength={1000} placeholder="逗号分隔，例如 产品, API, 使用指南" />
                <div className="settings-note">{"Agent 使用名称、描述和标签判断是否需要检索，分类阶段不读取文档正文。"}</div>
                <div className="surface-actions">
                  <button id="delete-knowledge-base-btn" className="button danger" type="button">{"删除知识库"}</button>
                  <div className="button-row">
                    <button id="create-knowledge-base-btn" className="button primary" type="button">{"创建知识库"}</button>
                    <button id="update-knowledge-base-btn" className="button secondary" type="button">{"保存修改"}</button>
                  </div>
                </div>
              </section>
            </section>
          </div>

          <div id="document-drawer" className="document-drawer" hidden={true}>
            <button id="document-drawer-backdrop" className="drawer-backdrop" type="button" aria-label="关闭文档详情"></button>
            <aside className="drawer-panel" role="dialog" aria-modal="true" aria-labelledby="document-drawer-title">
              <header className="drawer-header"><div ><span className="utility-label">{"Document detail"}</span><h2 id="document-drawer-title">{"文档详情"}</h2></div><button id="close-document-drawer" className="icon-button" type="button" aria-label="关闭文档详情"><svg className="app-icon" aria-hidden="true"><use href="#icon-close"></use></svg></button></header>
              <div className="drawer-body">
                <label htmlFor="document-title-input">{"标题"}<input id="document-title-input" maxLength={255} /></label>
                <label htmlFor="document-description-input">{"描述"}<textarea id="document-description-input" rows={4} maxLength={2000}></textarea></label>
                <label htmlFor="document-tags-input">{"标签"}<input id="document-tags-input" maxLength={1000} placeholder="逗号分隔" /></label>
                <dl id="document-metadata-list" className="document-metadata-list"></dl>
                <div id="document-index-error" className="document-index-error" hidden={true}></div>
                <div className="replace-document-block">
                  <label htmlFor="replace-document-file">{"替换文件"}<input id="replace-document-file" type="file" accept=".pdf,.docx,.md,.markdown,.txt,.html,.json,.yaml,.yml,.toml,.py,.js,.jsx,.ts,.tsx,.css,.go,.java,.rs" /></label>
                  <p >{"替换会重新解析和建索引；失败时继续使用旧内容。"}</p>
                  <button id="replace-document-btn" className="button secondary" type="button">{"替换并重建索引"}</button>
                </div>
              </div>
              <footer className="drawer-footer"><button id="delete-document-btn" className="button danger" type="button">{"删除文档"}</button><button id="save-document-btn" className="button primary" type="button">{"保存元数据"}</button></footer>
            </aside>
          </div>
        </ViewPanel>);
});
