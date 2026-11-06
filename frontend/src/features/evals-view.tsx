import { memo } from 'react';
import { ViewPanel } from '../components/ViewPanel';

export const EvaluationsView = memo(function EvaluationsView() {
 return (<ViewPanel id="evals-view" aria-labelledby="evals-title" view="evals" className="view-panel">
          <header className="view-header compact">
            <div >

              <h1 id="evals-title">{"评测"}</h1>
              <p >{"按约束判定 Agent 的执行过程，并程序化校验引用真实性。fake 免费，真实模型按 Token 计费。"}</p>
            </div>
            <div className="eval-launcher">
              <label className="eval-provider-field" htmlFor="eval-provider-select">{"Provider\n                "}<select id="eval-provider-select">
                  <option value="fake">{"fake（离线，零成本）"}</option>
                </select>
              </label>
              <button id="run-eval-btn" className="button primary" type="button">{"运行评测"}</button>
              <button id="refresh-eval-btn" className="button secondary" type="button">{"刷新"}</button>
            </div>
          </header>

          <div id="eval-progress" className="eval-progress" hidden={true}>
            <div className="eval-progress-bar"><span id="eval-progress-fill"></span></div>
            <p id="eval-progress-label">{"正在运行…"}</p>
          </div>

          <article className="surface eval-alert-surface">
            <div className="surface-heading">
              <div ><h2 >{"预警"}</h2><p >{"失败用例与硬性不变量始终报警；性能指标只与该 Provider 自己的基线比较。"}</p></div>
              <span id="eval-alert-count" className="meta-badge">{"0"}</span>
            </div>
            <div id="eval-alert-list" className="eval-alert-list"><div className="empty-state">{"尚未运行评测"}</div></div>
          </article>

          <div id="eval-metric-grid" className="metrics-grid eval-metric-grid">
            <div className="empty-state">{"尚未运行评测"}</div>
          </div>

          <article className="surface">
            <div className="surface-heading">
              <div ><h2 >{"用例结果"}</h2><p >{"约束不通过时显示破了哪一条；参考轨迹只作诊断。"}</p></div>
              <span id="eval-case-summary" className="meta-badge">{"0/0"}</span>
            </div>
            <div id="eval-case-list" className="eval-case-list"><div className="empty-state">{"尚未运行评测"}</div></div>
          </article>

          <article className="surface">
            <div className="surface-heading">
              <div ><h2 >{"历史"}</h2><p >{"点击任意一次查看详情；可把某次结果固化为该 Provider 的基线。"}</p></div>
              <button id="pin-eval-baseline-btn" className="text-button" type="button" disabled={true}>{"设为基线"}</button>
            </div>
            <div id="eval-history-list" className="eval-history-list"><div className="empty-state">{"暂无历史"}</div></div>
          </article>
        </ViewPanel>);
});
