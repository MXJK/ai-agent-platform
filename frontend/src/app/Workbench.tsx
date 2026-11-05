import { ReactSlot } from '../components/ReactSlot';
import { memo } from 'react';
import { ChatView } from '../features/chat-view';
import { KnowledgeView } from '../features/rag-view';
import { MemoryView } from '../features/memory-view';
import { ModelsView } from '../features/models-view';
import { ToolsView } from '../features/tools-view';
import { SessionsView } from '../features/sessions-view';
import { EvaluationsView } from '../features/evals-view';
import { AuditView } from '../features/trace-audit-view';
import { ActivityView } from '../features/activity-view';

export const Workbench = memo(function Workbench() {
 return (<>
    <svg className="icon-sprite" aria-hidden="true">
      <symbol id="icon-plus" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"></path></symbol>
      <symbol id="icon-sliders" viewBox="0 0 24 24"><path d="M4 7h10M18 7h2M14 5v4M4 17h2M10 17h10M8 15v4"></path></symbol>
      <symbol id="icon-panel" viewBox="0 0 24 24"><rect x="3.5" y="4" width="17" height="16" rx="2"></rect><path d="M15 4v16M8 9h3M8 13h3"></path></symbol>
      <symbol id="icon-external" viewBox="0 0 24 24"><path d="M14 5h5v5M19 5l-8 8M18 13v5a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"></path></symbol>
      <symbol id="icon-message" viewBox="0 0 24 24"><path d="M5 17.5 3.5 21l4.4-1.8c1.2.5 2.6.8 4.1.8 5 0 9-3.6 9-8s-4-8-9-8-9 3.6-9 8c0 2.1.8 4 2 5.5Z"></path><path d="M8 12h8M8 9h5"></path></symbol>
      <symbol id="icon-agent" viewBox="0 0 24 24"><rect x="4" y="6" width="16" height="13" rx="3"></rect><path d="M9 2h6M12 2v4M8 11h.01M16 11h.01M9 15h6"></path></symbol>
      <symbol id="icon-knowledge" viewBox="0 0 24 24"><ellipse cx="12" cy="6" rx="8" ry="3"></ellipse><path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"></path></symbol>
      <symbol id="icon-history" viewBox="0 0 24 24"><path d="M4 12a8 8 0 1 0 2.3-5.7L4 8.6M4 4v4.6h4.6M12 8v5l3 2"></path></symbol>
      <symbol id="icon-activity" viewBox="0 0 24 24"><path d="M3 12h4l2.2-6 4 12 2.2-6H21"></path></symbol>
      <symbol id="icon-gauge" viewBox="0 0 24 24"><path d="M4 18a8 8 0 1 1 16 0"></path><path d="M12 18 16 10"></path><path d="M4 18h16"></path></symbol>
      <symbol id="icon-model" viewBox="0 0 24 24"><path d="M12 3 4.5 7.2 12 11.5l7.5-4.3L12 3Z"></path><path d="m4.5 12 7.5 4.3 7.5-4.3M4.5 16.8 12 21l7.5-4.2"></path></symbol>
      <symbol id="icon-network" viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="6" rx="2"></rect><rect x="3" y="15" width="6" height="6" rx="2"></rect><rect x="15" y="15" width="6" height="6" rx="2"></rect><path d="M12 9v3M6 15v-3h12v3"></path></symbol>
      <symbol id="icon-branch" viewBox="0 0 24 24"><circle cx="7" cy="5" r="2"></circle><circle cx="17" cy="7" r="2"></circle><circle cx="7" cy="19" r="2"></circle><path d="M7 7v10M9 11h3a5 5 0 0 0 5-5"></path></symbol>
      <symbol id="icon-pause" viewBox="0 0 24 24"><path d="M8 5v14M16 5v14"></path></symbol>
      <symbol id="icon-paperclip" viewBox="0 0 24 24"><path d="m9 12 6.8-6.8a3 3 0 0 1 4.2 4.2l-9.1 9.1a5 5 0 0 1-7.1-7.1l8.5-8.5M7 15l8.4-8.4"></path></symbol>
      <symbol id="icon-arrow-up" viewBox="0 0 24 24"><path d="M12 19V5M6.5 10.5 12 5l5.5 5.5"></path></symbol>
      <symbol id="icon-arrow-down" viewBox="0 0 24 24"><path d="M12 5v14M6.5 13.5 12 19l5.5-5.5"></path></symbol>
      <symbol id="icon-arrow-right" viewBox="0 0 24 24"><path d="M5 12h14M13.5 6.5 19 12l-5.5 5.5"></path></symbol>
      <symbol id="icon-chevron-left" viewBox="0 0 24 24"><path d="m15 6-6 6 6 6"></path></symbol>
      <symbol id="icon-folder" viewBox="0 0 24 24"><path d="M3.5 7.5A2.5 2.5 0 0 1 6 5h4l2 2h6A2.5 2.5 0 0 1 20.5 9.5v7A2.5 2.5 0 0 1 18 19H6a2.5 2.5 0 0 1-2.5-2.5Z"></path></symbol>
      <symbol id="icon-stop" viewBox="0 0 24 24"><rect x="7" y="7" width="10" height="10" rx="1"></rect></symbol>
      <symbol id="icon-close" viewBox="0 0 24 24"><path d="m7 7 10 10M17 7 7 17"></path></symbol>
      <symbol id="icon-copy" viewBox="0 0 24 24"><rect x="8" y="8" width="11" height="11" rx="2"></rect><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"></path></symbol>
      <symbol id="icon-edit" viewBox="0 0 24 24"><path d="M4 20h4l10.5-10.5a2.1 2.1 0 0 0-4-4L4 16v4Z"></path><path d="m13.5 6.5 4 4"></path></symbol>
      <symbol id="icon-refresh" viewBox="0 0 24 24"><path d="M20 7v5h-5M4 17v-5h5"></path><path d="M18.2 12a7 7 0 0 0-12-4.9L4 9M5.8 12a7 7 0 0 0 12 4.9L20 15"></path></symbol>
      <symbol id="icon-more" viewBox="0 0 24 24"><circle cx="5" cy="12" r="1"></circle><circle cx="12" cy="12" r="1"></circle><circle cx="19" cy="12" r="1"></circle></symbol>
      <symbol id="icon-shield" viewBox="0 0 24 24"><path d="M12 3 5 6v5c0 4.7 2.8 8 7 10 4.2-2 7-5.3 7-10V6l-7-3Z"></path><path d="m9 12 2 2 4-5"></path></symbol>
    </svg>
    <a className="skip-link" href="#main-workspace">{"跳到主要内容"}</a>

    <header className="topbar">
      <div className="topbar-leading">
        <button id="toggle-sidebar-btn" className="icon-button panel-toggle panel-toggle-left" type="button" aria-label="隐藏主导航" aria-controls="primary-sidebar" aria-expanded="true" title="隐藏主导航">
          <svg className="app-icon" aria-hidden="true"><use href="#icon-panel"></use></svg>
        </button>
        <a className="brand" href="#chat" aria-label="Cogent 首页">
          <span className="brand-mark" aria-hidden="true">{"C"}</span>
          <span >
            <strong >{"Cogent"}</strong>
            <small >{"Agent engineering console"}</small>
          </span>
        </a>
      </div>

      <div className="topbar-context" aria-label="当前工作上下文">
        <span className="context-label">{"当前会话"}</span>
        <strong id="header-session-id">{"尚未创建"}</strong>
      </div>

      <div className="topbar-actions">
        <div id="health-pill" className="status-pill neutral" role="status" aria-live="polite">
          <span className="status-dot" aria-hidden="true"></span>
          <span >{"连接中"}</span>
        </div>
        <button id="create-session-btn" className="button secondary create-session-action" type="button" aria-label="新建会话">
          <svg className="app-icon" aria-hidden="true"><use href="#icon-plus"></use></svg>
          <span className="create-session-label">{"新建会话"}</span>
        </button>
        <button id="open-settings-btn" className="icon-button" type="button" aria-label="打开设置" title="设置">
          <svg className="app-icon" aria-hidden="true"><use href="#icon-sliders"></use></svg>
        </button>
        <button id="toggle-inspector-btn" className="icon-button panel-toggle" type="button" aria-label="显示最近会话" aria-controls="inspector-panel" aria-expanded="true" title="显示最近会话">
          <svg className="app-icon" aria-hidden="true"><use href="#icon-panel"></use></svg>
        </button>
        <a className="icon-button docs-action" href="/docs" aria-label="打开 API 文档" title="API 文档">
          <svg className="app-icon" aria-hidden="true"><use href="#icon-external"></use></svg>
        </a>
      </div>
    </header>

    <div className="app-shell">
      <aside className="sidebar" id="primary-sidebar">
        <div id="sidebar-resizer" className="panel-resizer panel-resizer-left" role="separator" aria-label="调整主导航宽度" aria-orientation="vertical" aria-valuemin={188} aria-valuemax={360} aria-valuenow={220} tabIndex={0}></div>
        <nav className="primary-nav" aria-label="主要功能">
          <button className="nav-item mobile-primary active" type="button" data-view="chat" aria-current="page">
            <span className="nav-icon" aria-hidden="true"><svg className="app-icon"><use href="#icon-message"></use></svg></span>
            <span ><strong >{"对话工作台"}</strong><small >{"Cogent 统一任务入口"}</small></span>
          </button>
          <button className="nav-item mobile-primary" type="button" data-view="rag">
            <span className="nav-icon" aria-hidden="true"><svg className="app-icon"><use href="#icon-knowledge"></use></svg></span>
            <span ><strong >{"知识库"}</strong><small >{"检索与问答"}</small></span>
          </button>
          <button className="nav-item mobile-overflow-nav" type="button" data-view="memory">
            <span className="nav-icon" aria-hidden="true"><svg className="app-icon"><use href="#icon-history"></use></svg></span>
            <span ><strong >{"记忆工作台"}</strong><small >{"项目、个人与对话记忆"}</small></span>
          </button>
          <button className="nav-item mobile-overflow-nav" type="button" data-view="models">
            <span className="nav-icon" aria-hidden="true"><svg className="app-icon"><use href="#icon-model"></use></svg></span>
            <span ><strong >{"模型管理"}</strong><small >{"Provider、模型与连接状态"}</small></span>
          </button>
          <button className="nav-item mobile-overflow-nav" type="button" data-view="tools">
            <span className="nav-icon" aria-hidden="true"><svg className="app-icon"><use href="#icon-network"></use></svg></span>
            <span ><strong >{"工具"}</strong><small >{"Skill 与 MCP 管理"}</small></span>
          </button>
          <button className="nav-item mobile-primary" type="button" data-view="sessions">
            <span className="nav-icon" aria-hidden="true"><svg className="app-icon"><use href="#icon-history"></use></svg></span>
            <span ><strong >{"会话记录"}</strong><small >{"历史消息与摘要"}</small></span>
          </button>
          <button className="nav-item mobile-overflow-nav" type="button" data-view="evals">
            <span className="nav-icon" aria-hidden="true"><svg className="app-icon"><use href="#icon-gauge"></use></svg></span>
            <span ><strong >{"评测"}</strong><small >{"轨迹约束、指标与预警"}</small></span>
          </button>
          <button className="nav-item mobile-overflow-nav" type="button" data-view="trace-audit">
            <span className="nav-icon" aria-hidden="true"><svg className="app-icon"><use href="#icon-shield"></use></svg></span>
            <span ><strong >{"Trace 审计"}</strong><small >{"工具、审批与状态转移"}</small></span>
          </button>
          <button className="nav-item mobile-primary" type="button" data-view="activity">
            <span className="nav-icon" aria-hidden="true"><svg className="app-icon"><use href="#icon-activity"></use></svg></span>
            <span ><strong >{"运行中心"}</strong><small >{"指标、请求与诊断"}</small></span>
          </button>
          <button id="mobile-more-btn" className="nav-item mobile-more-nav" type="button" aria-expanded="false" aria-controls="mobile-more-menu">
            <span className="nav-icon" aria-hidden="true"><svg className="app-icon"><use href="#icon-more"></use></svg></span>
            <span ><strong >{"更多"}</strong><small >{"更多工作台"}</small></span>
          </button>
        </nav>

        <button id="mobile-nav-backdrop" className="mobile-nav-backdrop" type="button" aria-label="关闭更多导航" hidden={true}></button>
        <div id="mobile-more-menu" className="mobile-more-menu" role="dialog" aria-label="更多工作台" hidden={true}>
          <div className="mobile-more-heading"><strong >{"更多工作台"}</strong><span >{"选择一个工作区"}</span></div>
          <button type="button" data-view="memory"><svg className="app-icon" aria-hidden="true"><use href="#icon-history"></use></svg><span ><strong >{"记忆工作台"}</strong><small >{"项目、个人与对话记忆"}</small></span></button>
          <button type="button" data-view="models"><svg className="app-icon" aria-hidden="true"><use href="#icon-model"></use></svg><span ><strong >{"模型管理"}</strong><small >{"Provider、模型与连接状态"}</small></span></button>
          <button type="button" data-view="tools"><svg className="app-icon" aria-hidden="true"><use href="#icon-network"></use></svg><span ><strong >{"工具"}</strong><small >{"Skill 与 MCP 管理"}</small></span></button>
          <button type="button" data-view="evals"><svg className="app-icon" aria-hidden="true"><use href="#icon-gauge"></use></svg><span ><strong >{"评测"}</strong><small >{"轨迹约束、指标与预警"}</small></span></button>
          <button type="button" data-view="trace-audit"><svg className="app-icon" aria-hidden="true"><use href="#icon-shield"></use></svg><span ><strong >{"Trace 审计"}</strong><small >{"工具、审批与状态转移"}</small></span></button>
          <button id="mobile-more-settings-btn" type="button"><svg className="app-icon" aria-hidden="true"><use href="#icon-sliders"></use></svg><span ><strong >{"工作区设置"}</strong><small >{"选择 Agent 操作的文件夹"}</small></span></button>
        </div>

        <div className="sidebar-context">
          <div className="context-card-heading">
            <span >{"工作上下文"}</span>
            <button id="sidebar-settings-btn" className="text-button" type="button">{"编辑"}</button>
          </div>
          <dl className="context-list">
            <div ><dt >{"模型"}</dt><dd id="context-model">{"默认配置"}</dd></div>
            <div ><dt >{"工作区"}</dt><dd id="context-workspace">{"未选择"}</dd></div>
          </dl>
          <p id="session-status" className="context-note">{"创建会话后即可开始工作"}</p>
        </div>
      </aside>

      <main id="main-workspace" className="workspace" tabIndex={-1}>
        <ChatView />

        <KnowledgeView />

        <MemoryView />

        <ModelsView />

        <ToolsView />

        <SessionsView />

        <EvaluationsView />

        <AuditView />

        <ActivityView />
      </main>

      <button id="inspector-backdrop" className="inspector-backdrop" type="button" aria-label="关闭最近会话" hidden={true}></button>
      <aside id="inspector-panel" className="inspector" aria-label="最近会话侧栏">
        <div id="inspector-resizer" className="panel-resizer panel-resizer-right" role="separator" aria-label="调整最近会话宽度" aria-orientation="vertical" aria-valuemin={240} aria-valuemax={440} aria-valuenow={288} tabIndex={0}></div>
        <header className="inspector-header">
          <div ><h2 >{"最近会话"}</h2><p >{"快速回到最近工作"}</p></div>
          <div className="inspector-header-actions">
            <button id="view-all-sessions-btn" className="text-button" type="button">{"查看全部"}</button>
            <button id="close-inspector-btn" className="icon-button small" type="button" aria-label="关闭最近会话">
              <svg className="app-icon" aria-hidden="true"><use href="#icon-close"></use></svg>
            </button>
          </div>
        </header>

        <section className="inspector-recent" aria-label="最近会话">
          <div id="recent-sessions-list" className="recent-sessions-list" aria-live="polite"><ReactSlot name="recent-sessions" /></div>
        </section>
      </aside>
    </div>

    <dialog id="settings-dialog" className="settings-dialog" aria-labelledby="settings-title">
      <form method="dialog" className="settings-shell">
        <input id="user-id-input" type="hidden" defaultValue="demo_user" />
        <input id="conversation-id-input" type="hidden" />
        <input id="provider-input" type="hidden" />
        <input id="model-input" type="hidden" />
        <input id="thinking-level-input" type="hidden" />
        <input id="workspace-id-input" type="hidden" />
        <input id="workspace-root-input" type="hidden" />
        <header className="dialog-header">
          <div ><p className="eyebrow">{"Local Workspaces"}</p><h2 id="settings-title">{"工作区管理"}</h2><p >{"选择 Agent 可以读取和操作的本地文件夹。"}</p></div>
          <button id="close-settings-btn" className="icon-button" type="submit" value="cancel" aria-label="关闭设置">
            <svg className="app-icon" aria-hidden="true"><use href="#icon-close"></use></svg>
          </button>
        </header>

        <div className="settings-body workspace-manager-body">
          <section className="workspace-manager-toolbar" aria-labelledby="workspace-library-title">
            <div >
              <span className="utility-label">{"Folder library"}</span>
              <h3 id="workspace-library-title">{"已添加的文件夹"}</h3>
              <p >{"选择一个文件夹作为当前会话的工作区。"}</p>
            </div>
            <button id="open-workspace-picker-btn" className="button primary" type="button">
              <svg className="app-icon" aria-hidden="true"><use href="#icon-folder"></use></svg>{"\n              添加文件夹\n            "}</button>
          </section>

          <label className="workspace-default-option" htmlFor="workspace-default-toggle">
            <input id="workspace-default-toggle" type="checkbox" />
            <span ><strong >{"同时设为新会话默认工作区"}</strong><small id="workspace-default-help">{"不勾选时，只切换当前会话。"}</small></span>
          </label>

          <div id="workspace-manager-list" className="workspace-manager-list" role="list" aria-live="polite">
            <div className="empty-state">{"正在加载工作区…"}</div>
          </div>
        </div>

        <footer className="dialog-footer">
          <p >{"移除只会取消文件夹注册，不会删除电脑文件、历史会话或项目记忆。"}</p>
          <div className="button-row">
            <button className="button secondary" type="submit" value="cancel">{"完成"}</button>
          </div>
        </footer>
      </form>
    </dialog>

    <dialog id="workspace-picker-dialog" className="settings-dialog workspace-picker-dialog" aria-labelledby="workspace-picker-title">
      <form method="dialog" className="settings-shell workspace-picker-shell">
        <header className="dialog-header">
          <div ><p className="eyebrow">{"Local Workspace"}</p><h2 id="workspace-picker-title">{"添加工作区文件夹"}</h2><p id="workspace-picker-description">{"系统窗口不可用时，可在这里选择服务允许访问的项目文件夹。"}</p></div>
          <button id="close-workspace-picker-btn" className="icon-button" type="submit" value="cancel" aria-label="关闭文件夹选择器">
            <svg className="app-icon" aria-hidden="true"><use href="#icon-close"></use></svg>
          </button>
        </header>

        <div className="workspace-picker-body">
          <div className="workspace-picker-toolbar">
            <button id="workspace-picker-up-btn" className="button ghost" type="button">
              <svg className="app-icon" aria-hidden="true"><use href="#icon-chevron-left"></use></svg>{"\n              上一级\n            "}</button>
            <button id="workspace-picker-roots-btn" className="button ghost" type="button">{"允许的位置"}</button>
          </div>
          <div className="workspace-picker-path">
            <span >{"当前位置"}</span>
            <code id="workspace-picker-current-path">{"允许的位置"}</code>
          </div>
          <div id="workspace-directory-list" className="workspace-directory-list" aria-live="polite">
            <div className="empty-state">{"正在加载文件夹…"}</div>
          </div>
        </div>

        <footer className="dialog-footer">
          <p >{"选择后会自动添加并切换；文件内容只在任务需要时读取。"}</p>
          <div className="button-row">
            <button className="button ghost" type="submit" value="cancel">{"取消"}</button>
            <button id="choose-workspace-directory-btn" className="button primary" type="button" disabled={true}>{"选择此文件夹"}</button>
          </div>
        </footer>
      </form>
    </dialog>

    <dialog id="checkpoint-history-dialog" className="settings-dialog checkpoint-history-dialog" aria-labelledby="checkpoint-history-title">
      <section className="settings-shell checkpoint-history-shell">
        <header className="dialog-header checkpoint-history-header">
          <div >
            <p className="eyebrow">{"Run time travel"}</p>
            <h2 id="checkpoint-history-title">{"从检查点继续"}</h2>
            <p >{"选择一个已完成的图节点，在当前会话回到此处，或分叉为一条新的执行路径。"}</p>
          </div>
          <button id="close-checkpoint-history-btn" className="icon-button" type="button" aria-label="关闭检查点历史">
            <svg className="app-icon" aria-hidden="true"><use href="#icon-close"></use></svg>
          </button>
        </header>

        <div className="checkpoint-history-body">
          <aside className="checkpoint-rail-pane" aria-labelledby="checkpoint-rail-title">
            <div className="checkpoint-rail-heading">
              <div ><span className="utility-label">{"Execution graph"}</span><h3 id="checkpoint-rail-title">{"Checkpoint 轨迹"}</h3></div>
              <code id="checkpoint-run-label">{"—"}</code>
            </div>
            <div id="checkpoint-history-list" className="checkpoint-history-list" role="listbox" aria-label="选择历史检查点">
              <div className="empty-state">{"正在读取 checkpoint…"}</div>
            </div>
          </aside>

          <article className="checkpoint-detail-pane" aria-live="polite">
            <div id="checkpoint-detail-empty" className="checkpoint-detail-empty">
              <span className="checkpoint-detail-mark" aria-hidden="true">{"◇"}</span>
              <strong >{"选择一个检查点"}</strong>
              <p >{"每个节点都保留自己的状态、下一步与父级关系。"}</p>
            </div>
            <div id="checkpoint-detail" hidden={true}>
              <div className="checkpoint-detail-heading">
                <div ><span id="checkpoint-detail-step" className="utility-label">{"STEP —"}</span><h3 id="checkpoint-detail-title">{"检查点"}</h3></div>
                <span id="checkpoint-detail-state" className="meta-badge">{"可恢复"}</span>
              </div>
              <p id="checkpoint-detail-summary" className="checkpoint-detail-summary"></p>
              <dl className="checkpoint-detail-grid">
                <div ><dt >{"Checkpoint"}</dt><dd ><code id="checkpoint-detail-id">{"—"}</code></dd></div>
                <div ><dt >{"下一节点"}</dt><dd id="checkpoint-detail-next">{"—"}</dd></div>
                <div ><dt >{"工具调用"}</dt><dd id="checkpoint-detail-tools">{"0"}</dd></div>
                <div ><dt >{"工作区变更"}</dt><dd id="checkpoint-detail-files">{"无"}</dd></div>
              </dl>
              <label className="checkpoint-direction-field" htmlFor="checkpoint-direction-input">
                <span >{"恢复后的新方向 "}<small >{"可选，将在下一个规划边界生效"}</small></span>
                <textarea id="checkpoint-direction-input" rows={3} maxLength={4000} placeholder="例如：从这里开始只补测试，不再改动实现"></textarea>
              </label>
              <div className="checkpoint-workspace-warning">
                <svg className="app-icon" aria-hidden="true"><use href="#icon-shield"></use></svg>
                <p ><strong >{"执行状态与文件状态分开。"}</strong>{"恢复 checkpoint 不会覆盖当前工作区文件；如需撤销文件修改，请先使用对应 Run 的 ChangeSet 回滚。"}</p>
              </div>
              <p id="checkpoint-action-error" className="inline-checkpoint-error" role="alert" hidden={true}></p>
              <div className="checkpoint-detail-actions">
                <button id="checkpoint-fork-btn" className="button secondary" type="button">
                  <svg className="app-icon" aria-hidden="true"><use href="#icon-branch"></use></svg>{"\n                  分叉为新会话\n                "}</button>
                <button id="checkpoint-rollback-btn" className="button primary" type="button">{"\n                  回到此处继续\n                  "}<svg className="app-icon" aria-hidden="true"><use href="#icon-arrow-right"></use></svg>
                </button>
              </div>
            </div>
          </article>
        </div>

        <footer className="dialog-footer checkpoint-history-footer">
          <p id="checkpoint-history-status">{"原 Run 与历史 checkpoint 会完整保留。"}</p>
          <button className="button ghost" id="checkpoint-history-done-btn" type="button">{"关闭"}</button>
        </footer>
      </section>
    </dialog>

    <div id="toast-region" className="toast-region" aria-live="polite" aria-atomic="true"></div>

    
  </>);
});
