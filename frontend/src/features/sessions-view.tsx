import { memo } from 'react';
import { ReactSlot } from '../components/ReactSlot';
import { ViewPanel } from '../components/ViewPanel';

export const SessionsView = memo(function SessionsView() {
 return (<ViewPanel id="sessions-view" aria-labelledby="sessions-title" view="sessions" className="view-panel">
          <header className="view-header compact">
            <div >

              <h1 id="sessions-title">{"会话记录"}</h1>
              <p >{"选择会话，在本页查看历史消息与摘要；支持搜索、归档和恢复。"}</p>
            </div>
            <div className="button-row">
              <button id="list-sessions-btn" className="button ghost" type="button">{"刷新"}</button>
              <button id="sessions-create-btn" className="button primary" type="button">{"新建会话"}</button>
            </div>
          </header>

          <div className="content-grid session-grid">
            <article className="surface">
              <div className="surface-heading"><h2 >{"会话"}</h2><span id="sessions-count" className="meta-badge">{"0"}</span></div>
              <form id="session-filter-form" className="session-filter-form" role="search">
                <label className="sr-only" htmlFor="session-search-input">{"搜索会话"}</label>
                <input id="session-search-input" type="search" maxLength={200} placeholder="搜索标题或消息正文" />
                <label className="sr-only" htmlFor="session-state-filter">{"会话状态"}</label>
                <select id="session-state-filter">
                  <option value="active">{"活跃会话"}</option>
                  <option value="archived">{"已归档"}</option>
                </select>
                <button className="button ghost" type="submit">{"搜索"}</button>
              </form>
              <div id="sessions-list" className="session-list"><ReactSlot name="sessions" /></div>
              <button id="load-more-sessions-btn" className="button ghost session-load-more" type="button" hidden={true}>{"加载更多"}</button>
            </article>
            <article className="surface session-detail">
              <div className="surface-heading">
                <h2 >{"消息详情"}</h2>
                <div className="button-row">
                  <button id="summary-session-btn" className="text-button" type="button">{"摘要"}</button>
                  <button id="refresh-messages-btn" className="text-button" type="button">{"刷新"}</button>
                </div>
              </div>
              <div id="session-summary" className="session-summary"><div className="empty-state">{"选择一个会话查看详情"}</div></div>
              <div id="messages-list" className="message-list"></div>
              <details className="developer-message">
                <summary >{"添加测试消息"}</summary>
                <div className="field-grid">
                  <label htmlFor="message-role-input">{"角色\n                    "}<select id="message-role-input">
                      <option value="user">{"user"}</option>
                      <option value="assistant">{"assistant"}</option>
                      <option value="system">{"system"}</option>
                      <option value="tool">{"tool"}</option>
                    </select>
                  </label>
                </div>
                <label htmlFor="message-content-input">{"消息内容"}</label>
                <textarea id="message-content-input" rows={3} defaultValue={"调试消息：请记录当前会话状态"} />
                <button id="add-message-btn" className="button secondary" type="button">{"添加消息"}</button>
              </details>
            </article>
          </div>
        </ViewPanel>);
});
