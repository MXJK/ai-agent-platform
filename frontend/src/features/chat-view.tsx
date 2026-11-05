import { memo } from 'react';
import { ViewPanel } from '../components/ViewPanel';

export const ChatView = memo(function ChatView() {
 return (<ViewPanel id="chat-view" aria-labelledby="chat-title" view="chat" className="view-panel chat-workbench">
          <header id="chat-welcome-header" className="view-header chat-welcome-header">
            <div >
              <p className="eyebrow">{"Agent Command Deck"}</p>
              <h1 id="chat-title">{"把任务交给 Agent，"}<br  />{"每一步仍由你掌控。"}</h1>
              <p >{"从问题、计划到执行与验证，全部保留在同一个可追溯会话中。"}</p>
            </div>
          </header>

          <header id="active-session-header" className="active-session-header" hidden={true}>
            <div className="active-session-heading">
              <span className="active-session-mark" aria-hidden="true"><i ></i></span>
              <div >
                <p >{"Active conversation"}</p>
                <h1 id="active-session-title">{"当前会话"}</h1>
              </div>
            </div>
            <div className="active-session-actions">
              <span id="chat-meta" className="status-pill neutral" role="status" aria-live="polite">
                <span className="status-dot" aria-hidden="true"></span><span >{"等待输入"}</span>
              </span>
              <button id="rename-current-session-btn" className="icon-button small" type="button" aria-label="重命名当前会话" title="重命名当前会话">
                <svg className="app-icon" aria-hidden="true"><use href="#icon-edit"></use></svg>
              </button>
            </div>
          </header>

          <div id="chat-output" className="conversation" aria-label="对话内容" tabIndex={0}>
            <div className="welcome-state">
              <div className="welcome-signal" aria-hidden="true">
                <span className="active"><i ></i><b >{"ASK"}</b></span>
                <span ><i ></i><b >{"PLAN"}</b></span>
                <span ><i ></i><b >{"ACT"}</b></span>
                <span ><i ></i><b >{"VERIFY"}</b></span>
              </div>
              <div className="welcome-content">
                <div className="welcome-mark" aria-hidden="true"><strong >{"AGENT"}</strong><span >{"READY"}</span></div>
                <h2 >{"从一个具体问题开始"}</h2>
                <p >{"由 Cogent 理解上下文并完成任务；需要确认的操作会在执行前暂停。"}</p>
                <div className="prompt-grid" aria-label="推荐问题">
                  <button type="button" className="prompt-card" data-prompt="解释这个项目的核心架构和请求调用链">
                    <span >{"理解项目"}</span><strong >{"解释核心架构和请求调用链"}</strong>
                  </button>
                  <button type="button" className="prompt-card" data-prompt="帮我分析 SSE 流式输出的实现与异常处理">
                    <span >{"分析实现"}</span><strong >{"检查 SSE 流式输出"}</strong>
                  </button>
                  <button type="button" className="prompt-card" data-prompt="为这个项目设计一套可靠的测试策略">
                    <span >{"规划质量"}</span><strong >{"设计可靠的测试策略"}</strong>
                  </button>
                </div>
              </div>
            </div>
          </div>

          <div className="composer-wrap">
            <button id="jump-to-latest-btn" className="jump-to-latest button secondary" type="button" hidden={true}>{"\n              回到底部\n              "}<svg className="app-icon" aria-hidden="true"><use href="#icon-arrow-down"></use></svg>
            </button>
            <div id="archived-session-notice" className="archived-session-notice" hidden={true}>
              <span >{"这是已归档会话，只能查看历史消息。"}</span>
              <button id="restore-current-session-btn" className="text-button" type="button">{"恢复并继续"}</button>
            </div>
            <div id="composer-scope-strip" className="composer-scope-strip" aria-label="当前执行上下文">
              <button id="composer-workspace-btn" className="scope-chip workspace-scope" type="button">
                <span id="composer-workspace-status-dot" className="workspace-status-dot is-missing" aria-hidden="true"></span>
                <span className="scope-chip-copy"><small >{"Workspace"}</small><strong id="composer-workspace-label">{"未选择工作区"}</strong></span>
                <em id="composer-workspace-role">{"选择"}</em>
              </button>
              <button id="composer-model-btn" className="scope-chip model-scope" type="button">
                <svg className="app-icon" aria-hidden="true"><use href="#icon-model"></use></svg>
                <span className="scope-chip-copy"><small >{"Model"}</small><strong id="composer-model-label">{"默认配置"}</strong></span>
                <em >{"配置"}</em>
              </button>
              <button id="composer-context-budget" className="scope-chip context-scope" type="button" aria-live="polite" aria-expanded="false" aria-controls="composer-context-ring-tooltip">
                <svg className="app-icon" aria-hidden="true"><use href="#icon-shield"></use></svg>
                <span className="scope-chip-copy">
                  <small id="composer-context-kicker">{"本会话累计 Token"}</small>
                  <strong id="composer-context-label">
                    <span id="composer-context-label-full" className="composer-context-label-full">{"等待首轮请求"}</span>
                    <span id="composer-context-label-compact" className="composer-context-label-compact">{"等待首轮请求"}</span>
                  </strong>
                </span>
                <span id="composer-context-percentage" className="context-percentage" aria-hidden="true">{"–"}</span>
                <span id="composer-context-ring" className="context-ring" aria-hidden="true">
                  <svg className="context-ring-svg" viewBox="0 0 36 36">
                    <circle className="context-ring-track" cx="18" cy="18" r="15.5"></circle>
                    <circle id="composer-context-ring-fill" className="context-ring-fill" cx="18" cy="18" r="15.5"></circle>
                  </svg>
                  <strong id="composer-context-ring-label">{"–"}</strong>
                </span>
              </button>
            </div>
            <div className="composer">
              <div id="slash-command-menu" className="slash-command-menu" role="listbox" aria-label="可用命令" hidden={true}>
                <div className="slash-command-heading">
                  <strong id="slash-command-title">{"命令与能力"}</strong>
                  <span >{"↑↓ 选择 · Enter 使用 · Esc 关闭"}</span>
                </div>
                <div id="slash-command-options" className="slash-command-options"></div>
                <p id="slash-command-status" className="slash-command-status" role="status" aria-live="polite"></p>
              </div>
              <div className="composer-mode-bar">
                <div className="composer-tools">
                  <details id="composer-config" className="composer-config">
                    <summary >
                      <svg className="app-icon" aria-hidden="true"><use href="#icon-sliders"></use></svg>{"\n                      运行配置\n                    "}</summary>
                    <div className="composer-config-panel">
                      <label className="composer-mode-control model-auto-control" htmlFor="auto-model-toggle">
                        <input id="auto-model-toggle" type="checkbox" defaultChecked={true} />
                        <span >{"Agent 自动选模"}</span>
                      </label>
                      <div className="composer-mode-control model-choice-control">
                    <span id="session-model-label">{"路由策略"}</span>
                    <select id="session-model-select" aria-label="会话模型或自动路由策略">
                      <option value="smart">{"智能 · 按任务难度"}</option>
                      <option value="quality">{"质量优先"}</option>
                      <option value="cost">{"成本优先"}</option>
                      <option value="latency">{"速度优先"}</option>
                    </select>
                    <button id="model-picker-trigger" className="model-picker-trigger" type="button" aria-haspopup="listbox" aria-controls="model-picker-menu" aria-expanded="false" hidden={true}>
                      <span id="model-picker-trigger-content" className="model-picker-trigger-content">{"选择首选模型"}</span>
                      <span className="model-picker-chevron" aria-hidden="true"></span>
                    </button>
                    <div id="model-picker-menu" className="model-picker-menu" role="listbox" aria-label="首选模型" hidden={true}>
                      <div className="model-picker-legend" aria-label="延迟颜色说明">
                        <span ><i className="latency-dot fast"></i>{"≤ 1000 ms"}</span>
                        <span ><i className="latency-dot moderate"></i>{"≤ 3000 ms"}</span>
                        <span ><i className="latency-dot slow"></i>{"> 3000 ms"}</span>
                      </div>
                      <div id="model-picker-options" className="model-picker-options"></div>
                    </div>
                      </div>
                      <label id="model-fallback-control" className="composer-mode-control model-auto-control" htmlFor="model-fallback-toggle" hidden={true}>
                        <input id="model-fallback-toggle" type="checkbox" defaultChecked={true} />
                        <span >{"不可用时 fallback"}</span>
                      </label>
                      <label id="approval-mode-control" className="composer-mode-control approval-mode-control" htmlFor="permission-mode-input">
                        <span >{"权限模式"}</span>
                        <select id="permission-mode-input" aria-label="Cogent 权限模式">
                          <option value="default">{"逐次确认"}</option>
                          <option value="acceptEdits">{"接受文件编辑"}</option>
                          <option value="plan">{"只读规划"}</option>
                          <option value="bypassPermissions">{"跳过普通确认"}</option>
                        </select>
                        <em id="permission-mode-hint" className="approval-mode-hint">{"只读自动执行；写入和命令逐次确认"}</em>
                      </label>
                    </div>
                  </details>
                </div>
                <span id="composer-mode-description">{"Cogent 统一处理对话和代码任务；权限边界始终生效。"}</span>
              </div>
              <label className="sr-only" htmlFor="chat-message-input">{"输入消息"}</label>
              <textarea id="chat-message-input" rows={2} maxLength={8000} placeholder="输入消息，键入 / 使用 Skill 或 MCP，Enter 发送…" aria-autocomplete="list" aria-controls="slash-command-menu" aria-expanded="false"></textarea>
              <div className="composer-footer">
                <div className="composer-hints">
                  <span id="composer-context">{"默认模型"}</span>
                  <span >{"/ 调用命令与能力"}</span>
                  <span >{"内容上限 8,000 字符"}</span>
                </div>
                <div className="button-row">
                  <button id="stop-chat-btn" className="button ghost hidden" type="button">
                    <svg className="app-icon" aria-hidden="true"><use href="#icon-stop"></use></svg>{"\n                    停止\n                  "}</button>
                  <button id="agent-run-control-btn" className="icon-button composer-run-control" type="button" aria-label="暂停 Agent" title="暂停 Agent" hidden={true}>
                    <svg className="app-icon" aria-hidden="true"><use href="#icon-pause"></use></svg>
                  </button>
                  <button id="send-chat-btn" className="button primary" type="button">{"\n                    发送\n                    "}<svg className="app-icon" aria-hidden="true"><use href="#icon-arrow-up"></use></svg>
                  </button>
                </div>
              </div>
            </div>
          </div>
        </ViewPanel>);
});
