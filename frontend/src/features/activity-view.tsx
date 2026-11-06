import { memo } from 'react';
import { ViewPanel } from '../components/ViewPanel';

export const ActivityView = memo(function ActivityView() {
 return (<ViewPanel id="activity-view" aria-labelledby="activity-title" view="activity" className="view-panel">
          <header className="view-header compact">
            <div >

              <h1 id="activity-title">{"运行中心"}</h1>
              <p >{"观察本地服务状态、最近请求与任务运行情况。"}</p>
            </div>
            <button id="refresh-overview-btn" className="button secondary" type="button">{"刷新数据"}</button>
          </header>

          <div className="metrics-grid">
            <article className="metric-card"><span >{"API 状态"}</span><strong id="metric-api">{"连接中"}</strong><small >{"应用服务健康度"}</small></article>
            <article className="metric-card"><span >{"会话数量"}</span><strong id="metric-sessions">{"0"}</strong><small >{"当前筛选中的持久化记录"}</small></article>
            <article className="metric-card"><span >{"最新 Agent"}</span><strong id="metric-run">{"暂无"}</strong><small >{"最近一次运行状态"}</small></article>
            <article className="metric-card"><span >{"最近请求"}</span><strong id="metric-request">{"暂无"}</strong><small >{"状态码与耗时"}</small></article>
          </div>

          <article className="surface token-usage-surface">
            <div className="surface-heading">
              <div >
                <h2 >{"Workspace Token 用量"}</h2>
                <p >{"累计 Chat 与 Code Agent 的供应商 Usage；不含项目记忆提炼任务。"}</p>
              </div>
              <span className="meta-badge">{"累计"}</span>
            </div>
            <div id="workspace-token-list" className="workspace-token-grid">
              <div className="empty-state">{"暂无 Workspace 用量"}</div>
            </div>
          </article>

          <article className="surface request-surface">
            <div className="surface-heading">
              <div ><h2 >{"请求日志"}</h2><p >{"仅保留当前浏览器会话中的最近 50 条记录。"}</p></div>
              <button id="clear-log-btn" className="text-button" type="button">{"清空"}</button>
            </div>
            <div id="request-log" className="request-list"><div className="empty-state">{"暂无请求"}</div></div>
          </article>
        </ViewPanel>);
});
