import { memo } from 'react';
import { ViewPanel } from '../components/ViewPanel';

export const AuditView = memo(function AuditView() {
 return (<ViewPanel id="trace-audit-view" aria-labelledby="trace-audit-title" view="trace-audit" className="view-panel">
          <header className="view-header compact trace-audit-page-header">
            <div >
              <h1 id="trace-audit-title">{"Trace 审计"}</h1>
              <p >{"按 Run 复盘 Agent 的工具选择、精确参数、执行结果、错误、审批和状态转移。"}</p>
            </div>
            <button id="refresh-trace-audit-btn" className="button secondary" type="button">
              <svg className="app-icon" aria-hidden="true"><use href="#icon-refresh"></use></svg>{"\n              刷新 Trace\n            "}</button>
          </header>

          <div className="trace-audit-layout">
            <aside className="trace-run-browser" aria-labelledby="trace-run-browser-title">
              <div className="trace-run-browser-heading">
                <div ><h2 id="trace-run-browser-title">{"最近 Run"}</h2><p id="trace-run-count">{"正在加载…"}</p></div>
              </div>
              <div className="trace-run-filters">
                <label htmlFor="trace-run-search">{"搜索 Run 或会话"}</label>
                <input id="trace-run-search" type="search" placeholder="run_… / session_…" spellCheck="false" />
                <label htmlFor="trace-run-status-filter">{"状态"}</label>
                <select id="trace-run-status-filter">
                  <option value="all">{"全部状态"}</option>
                  <option value="active">{"运行中与挂起"}</option>
                  <option value="completed">{"已完成"}</option>
                  <option value="failed">{"失败或受阻"}</option>
                  <option value="cancelled">{"已取消"}</option>
                </select>
              </div>
              <div id="trace-run-list" className="trace-run-list" aria-live="polite" aria-busy="true">
                <div className="trace-audit-skeleton" aria-label="正在加载最近 Run"><span ></span><span ></span><span ></span></div>
              </div>
            </aside>

            <section className="trace-audit-detail" aria-labelledby="trace-audit-detail-title">
              <div id="trace-audit-empty" className="trace-audit-empty">
                <svg className="app-icon" aria-hidden="true"><use href="#icon-shield"></use></svg>
                <h2 id="trace-audit-detail-title">{"选择一次 Run 开始审计"}</h2>
                <p >{"这里会按追加式事件顺序展示状态、节点、工具、审批和错误事实。"}</p>
              </div>

              <div id="trace-audit-content" hidden={true}>
                <header className="trace-audit-detail-header">
                  <div >
                    <div className="trace-audit-title-line">
                      <h2 id="trace-audit-run-id">{"—"}</h2>
                      <span id="trace-audit-status" className="status-pill neutral"><span className="status-dot" aria-hidden="true"></span>{"未知"}</span>
                    </div>
                    <p id="trace-audit-node">{"等待 Run 详情…"}</p>
                  </div>
                  <span id="trace-audit-live" className="trace-live-indicator" hidden={true}><span aria-hidden="true"></span>{"实时更新"}</span>
                </header>

                <dl className="trace-audit-meta">
                  <div ><dt >{"会话"}</dt><dd id="trace-audit-session">{"—"}</dd></div>
                  <div ><dt >{"工作区"}</dt><dd id="trace-audit-workspace">{"—"}</dd></div>
                  <div ><dt >{"Checkpoint"}</dt><dd id="trace-audit-checkpoint">{"—"}</dd></div>
                  <div ><dt >{"事件游标"}</dt><dd id="trace-audit-cursor">{"—"}</dd></div>
                </dl>

                <div className="trace-audit-counts" aria-label="Trace 摘要">
                  <span ><strong id="trace-count-events">{"0"}</strong>{"事件"}</span>
                  <span ><strong id="trace-count-tools">{"0"}</strong>{"工具"}</span>
                  <span ><strong id="trace-count-approvals">{"0"}</strong>{"审批"}</span>
                  <span ><strong id="trace-count-errors">{"0"}</strong>{"错误"}</span>
                </div>

                <div className="trace-audit-toolbar">
                  <div className="trace-audit-category-filter" role="group" aria-label="筛选审计事件">
                    <button className="active" type="button" data-audit-filter="all" aria-pressed="true">{"全部"}</button>
                    <button type="button" data-audit-filter="state" aria-pressed="false">{"状态"}</button>
                    <button type="button" data-audit-filter="tool" aria-pressed="false">{"工具"}</button>
                    <button type="button" data-audit-filter="approval" aria-pressed="false">{"审批"}</button>
                    <button type="button" data-audit-filter="error" aria-pressed="false">{"错误"}</button>
                  </div>
                  <span id="trace-audit-visible-count">{"0 条"}</span>
                </div>

                <nav className="trace-audit-pagination" aria-label="审计事件分页">
                  <button id="trace-audit-prev" className="button secondary" type="button" disabled={true}>{"上一页"}</button>
                  <span id="trace-audit-page-label" aria-live="polite">{"1 / 1 页"}</span>
                  <button id="trace-audit-next" className="button secondary" type="button" disabled={true}>{"下一页"}</button>
                </nav>
                <ol id="trace-audit-timeline" className="trace-audit-timeline" aria-live="polite" aria-atomic="false">
                  <li className="trace-audit-placeholder">{"正在装载审计事实…"}</li>
                </ol>
              </div>
            </section>
          </div>
        </ViewPanel>);
});
