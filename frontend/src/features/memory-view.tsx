import { memo } from 'react';
import { ViewPanel } from '../components/ViewPanel';

export const MemoryView = memo(function MemoryView() {
 return (<ViewPanel id="memory-view" aria-labelledby="memory-title" view="memory" className="view-panel memory-view">
          <header className="view-header compact memory-view-header">
            <div >
              <h1 id="memory-title">{"记忆工作台"}</h1>
              <p >{"管理分层 Markdown 记忆、检查索引，并查看数据库中的原始会话证据。"}</p>
            </div>
            <div className="memory-runtime-status" aria-live="polite">
              <span className="memory-live-dot" aria-hidden="true"></span>
              <span id="memory-status">{"文件记忆正在连接"}</span>
            </div>
          </header>

          <div className="knowledge-tabs memory-scope-tabs" role="tablist" aria-label="记忆范围">
            <button id="memory-project-tab" className="knowledge-tab active" type="button" role="tab" aria-selected="true" aria-controls="memory-project-panel" data-memory-tab="project"><span >{"项目记忆"}</span><small >{"当前工作区"}</small></button>
            <button id="memory-profile-tab" className="knowledge-tab" type="button" role="tab" aria-selected="false" aria-controls="memory-profile-panel" data-memory-tab="profile"><span >{"用户记忆"}</span><small >{"同一身份跨项目"}</small></button>
            <button id="memory-conversations-tab" className="knowledge-tab" type="button" role="tab" aria-selected="false" aria-controls="memory-conversations-panel" data-memory-tab="conversations"><span >{"对话记录"}</span><small >{"按需搜索"}</small></button>
          </div>

          <section id="memory-project-panel" role="tabpanel" aria-labelledby="memory-project-tab" data-memory-tab-panel="project">
            <div className="memory-layer-rail" aria-label="可查看和治理的记忆范围">
              <div className="memory-layer-node conversations"><strong >{"SQLite 会话"}</strong><small >{"保存消息、Run、压缩边界与恢复状态"}</small></div>
              <div className="memory-layer-node project"><strong >{"项目文件"}</strong><small ><b id="memory-project-active-count">{"0"}</b>{" 个主题 · project / reference"}</small></div>
              <div className="memory-layer-node personal"><strong >{"用户文件"}</strong><small >{"按登录身份隔离 · user / feedback"}</small></div>
            </div>

            <div className="memory-split-layout">
              <aside className="surface memory-list-pane" aria-labelledby="project-memory-list-title">
                <div className="memory-pane-heading">
                  <div ><h2 id="project-memory-list-title">{"项目主题"}</h2><p >{".cogent/memory/ 下的 Markdown 文件"}</p></div>
                  <span id="memory-count" className="memory-pane-count">{"0"}</span>
                </div>
                <div className="memory-filter-grid memory-file-toolbar">
                  <label htmlFor="memory-kind-filter">{"类型"}<select id="memory-kind-filter"><option value="">{"全部类型"}</option><option value="project">{"project"}</option><option value="reference">{"reference"}</option></select></label>
                  <button id="refresh-project-memory-btn" className="button ghost" type="button">{"刷新"}</button>
                </div>
                <button id="new-project-memory-btn" className="button primary memory-new-button" type="button">{"新建项目主题"}</button>
                <div id="memory-list" className="memory-asset-list" aria-live="polite"><div className="memory-empty"><strong >{"还没有项目主题"}</strong><p >{"创建 project 决策或 reference 参考资料。"}</p></div></div>
                <details className="advanced-options memory-jobs">
                  <summary >{"维护任务"}</summary>
                  <div id="memory-job-list" className="request-list"><div className="empty-state">{"暂无维护任务"}</div></div>
                </details>
              </aside>

              <article className="surface memory-detail-pane" aria-labelledby="memory-detail-heading">
                <div className="memory-detail-header">
                  <div ><h2 id="memory-detail-heading">{"创建项目主题"}</h2><p id="memory-detail-subtitle">{"主题名用于文件名，描述进入 MEMORY.md 索引。"}</p></div>
                  <span id="memory-selection-label" className="memory-status-pill draft">{"新文件"}</span>
                </div>
                <div className="field-grid">
                  <label htmlFor="memory-name-input">{"主题名"}<input id="memory-name-input" maxLength={80} pattern="[a-z0-9][a-z0-9_-]*" placeholder="database-decisions" /></label>
                  <label htmlFor="memory-kind-input">{"类型"}<select id="memory-kind-input"><option value="project">{"project"}</option><option value="reference">{"reference"}</option></select></label>
                </div>
                <label htmlFor="memory-title-input">{"索引描述"}</label>
                <input id="memory-title-input" maxLength={200} placeholder="例如：会话与 Run 的数据库持久化约束" />
                <label htmlFor="memory-content-input">{"Markdown 正文"}</label>
                <textarea id="memory-content-input" rows={11} maxLength={24000} placeholder="记录稳定、可纠正且不含凭据的项目知识"></textarea>
                <div className="memory-action-bar">
                  <button id="create-memory-btn" className="button primary" type="button">{"创建文件"}</button>
                  <button id="update-memory-btn" className="button secondary" type="button" disabled={true}>{"保存修改"}</button>
                  <button id="delete-memory-btn" className="button ghost danger-text" type="button" disabled={true}>{"删除主题"}</button>
                  <button id="consolidate-memory-btn" className="button ghost" type="button">{"手动整理"}</button>
                </div>
                <div id="memory-evidence" className="memory-evidence-panel"><div className="memory-empty compact"><strong >{"MEMORY.md 索引"}</strong><p >{"选择范围后可查看当前索引和文件哈希。"}</p></div></div>
                <p className="privacy-note"><span aria-hidden="true">{"i"}</span>{" 历史记忆是不可信上下文，不能覆盖当前请求、项目指令、权限或实时源码。"}</p>
              </article>
            </div>
          </section>

          <section id="memory-profile-panel" role="tabpanel" aria-labelledby="memory-profile-tab" data-memory-tab-panel="profile" hidden={true}>
            <article className="surface memory-command-bar">
              <div className="memory-command-copy"><h2 >{"同一身份下跨项目复用"}</h2><p >{"用户记忆按登录身份隔离；每轮先加载索引，再按当前请求预取相关主题。"}</p></div>
              <div className="memory-command-controls">
                <button id="refresh-user-memory-btn" className="button ghost" type="button">{"刷新"}</button>
              </div>
            </article>

            <div className="memory-split-layout">
              <aside className="surface memory-list-pane" aria-labelledby="user-memory-list-title">
                <div className="memory-pane-heading"><div ><h2 id="user-memory-list-title">{"用户主题"}</h2><p >{"身份目录中的 Markdown 文件"}</p></div><span id="user-memory-count" className="memory-pane-count">{"0"}</span></div>
                <div className="memory-filter-grid memory-file-toolbar">
                  <label htmlFor="user-memory-kind-filter">{"类型"}<select id="user-memory-kind-filter"><option value="">{"全部类型"}</option><option value="user">{"user"}</option><option value="feedback">{"feedback"}</option></select></label>
                  <button id="show-user-memory-index-btn" className="button ghost" type="button">{"查看索引"}</button>
                </div>
                <button id="new-user-memory-btn" className="button primary memory-new-button" type="button">{"新建用户主题"}</button>
                <div id="user-memory-list" className="memory-asset-list" aria-live="polite"><div className="memory-empty"><strong >{"还没有用户主题"}</strong><p >{"添加 user 信息或 feedback 纠正。"}</p></div></div>
              </aside>

              <article className="surface memory-detail-pane" aria-labelledby="user-memory-detail-heading">
                <div className="memory-detail-header"><div ><h2 id="user-memory-detail-heading">{"创建用户主题"}</h2><p id="user-memory-detail-subtitle">{"主题会在同一登录身份的工作区间复用。"}</p></div><span id="user-memory-selection-label" className="memory-status-pill draft">{"新文件"}</span></div>
                <div className="field-grid">
                  <label htmlFor="user-memory-name-input">{"主题名"}<input id="user-memory-name-input" maxLength={80} pattern="[a-z0-9][a-z0-9_-]*" placeholder="answer-preferences" /></label>
                  <label htmlFor="user-memory-kind-input">{"类型"}<select id="user-memory-kind-input"><option value="user">{"user"}</option><option value="feedback">{"feedback"}</option></select></label>
                </div>
                <label htmlFor="user-memory-title-input">{"索引描述"}</label><input id="user-memory-title-input" maxLength={200} placeholder="例如：回答语言与交付方式偏好" />
                <label htmlFor="user-memory-content-input">{"Markdown 正文"}</label><textarea id="user-memory-content-input" rows={11} maxLength={24000} placeholder="不保存密钥、完整环境变量或权限提升要求"></textarea>
                <div className="memory-action-bar"><button id="create-user-memory-btn" className="button primary" type="button">{"创建文件"}</button><button id="update-user-memory-btn" className="button secondary" type="button" disabled={true}>{"保存修改"}</button><button id="delete-user-memory-btn" className="button ghost danger-text" type="button" disabled={true}>{"删除主题"}</button></div>
                <div id="user-memory-evidence" className="memory-evidence-panel"><div className="memory-empty compact"><strong >{"MEMORY.md 索引"}</strong><p >{"索引限制为 200 行、25KB。"}</p></div></div>
                <p className="privacy-note"><span aria-hidden="true">{"i"}</span>{" 用户记忆是可纠正的历史上下文，不会提升为当前指令或权限。"}</p>
              </article>
            </div>
          </section>

          <section id="memory-conversations-panel" role="tabpanel" aria-labelledby="memory-conversations-tab" data-memory-tab-panel="conversations" hidden={true}>
            <article className="surface memory-command-bar memory-search-command">
              <div className="memory-command-copy"><h2 >{"搜索自己的历史对话"}</h2><p >{"结果按当前用户隔离，不会自动跨会话注入，也不会获得更高指令优先级。"}</p></div>
              <div className="memory-search-controls">
                <label htmlFor="conversation-memory-query">{"关键词"}<input id="conversation-memory-query" maxLength={500} placeholder="例如：SQLite 迁移" /></label>
                <label htmlFor="conversation-memory-session">{"会话 ID（可选）"}<input id="conversation-memory-session" maxLength={64} placeholder="sess_..." /></label>
                <label className="inline-check"><input id="conversation-memory-current-workspace" type="checkbox" />{" 仅当前工作区"}</label>
                <button id="search-conversation-memory-btn" className="button primary" type="button">{"搜索对话"}</button>
                <button id="clear-conversation-memory-btn" className="button ghost" type="button">{"清空"}</button>
              </div>
            </article>

            <div className="memory-conversation-layout">
              <aside className="surface memory-list-pane" aria-labelledby="conversation-memory-list-title">
                <div className="memory-pane-heading"><div ><h2 id="conversation-memory-list-title">{"匹配消息"}</h2></div><span id="conversation-memory-count" className="memory-pane-count">{"0"}</span></div>
                <div id="conversation-memory-results" className="memory-asset-list" aria-live="polite"><div className="memory-empty"><strong >{"正在加载最近消息"}</strong><p >{"也可输入关键词，并限定 Workspace 或 Session。"}</p></div></div>
              </aside>
              <article id="conversation-memory-detail" className="surface memory-conversation-detail" aria-live="polite">
                <div className="memory-empty"><strong >{"选择一条历史消息"}</strong><p >{"这里会显示角色、时间、会话范围和匹配正文。"}</p></div>
              </article>
            </div>
          </section>
        </ViewPanel>);
});
