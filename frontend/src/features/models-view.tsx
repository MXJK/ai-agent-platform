import { memo } from 'react';
import { ReactSlot } from '../components/ReactSlot';
import { ViewPanel } from '../components/ViewPanel';

export const ModelsView = memo(function ModelsView() {
 return (<ViewPanel id="models-view" aria-labelledby="models-title" view="models" className="view-panel">
          <header className="view-header compact">
            <div >
              <p className="eyebrow">{"Global Model Registry"}</p>
              <h1 id="models-title">{"模型管理"}</h1>
              <p >{"全局配置一次，所有本地 Workspace 共用。密钥写入系统钥匙串，不会回显。"}</p>
            </div>
            <button id="refresh-model-registry-btn" className="button secondary" type="button">{"刷新状态"}</button>
          </header>

          <article className="surface">
            <div className="surface-heading">
              <div ><span className="step-kicker">{"01 · 连接"}</span><h2 >{"Provider 凭证"}</h2><p id="model-probe-policy-note">{"页面状态会自动刷新；周期探测默认关闭，单次测速会产生一次最短模型调用。"}</p></div>
            </div>
            <div id="provider-connection-grid" className="provider-connection-grid"><ReactSlot name="providers" /></div>
          </article>

          <div className="content-grid model-registry-grid">
            <article className="surface">
              <div className="surface-heading">
                <div ><span className="step-kicker">{"02 · 注册"}</span><h2 >{"选择模型"}</h2><p >{"保存 Provider 后发现当前 API Key 可用的文本生成模型。"}</p></div>
                <button id="discover-provider-models-btn" className="text-button" type="button">{"重新发现"}</button>
              </div>
              <div className="model-registration-flow">
                <label htmlFor="registered-model-provider-input">{"Provider\n                  "}<select id="registered-model-provider-input">
                    <option value="openai">{"OpenAI"}</option>
                    <option value="deepseek">{"DeepSeek"}</option>
                    <option value="anthropic">{"Anthropic"}</option>
                    <option value="google">{"Google"}</option>
                    <option value="glm">{"智谱 GLM"}</option>
                    <option value="minimax">{"MiniMax"}</option>
                    <option value="doubao">{"豆包 · 火山方舟"}</option>
                  </select>
                </label>
                <label htmlFor="discovered-model-select">{"可用模型\n                  "}<select id="discovered-model-select" disabled={true}>
                    <option value="">{"请先保存 Provider 配置"}</option>
                  </select>
                </label>
              </div>
              <div id="discovered-model-summary" className="model-discovery-summary" aria-live="polite">{"后端会自动维护显示名称、能力、上下文和路由参数。"}</div>
              <details className="advanced-options model-manual-fallback">
                <summary >{"列表中没有目标模型？手动填写模型 ID"}</summary>
                <label htmlFor="manual-model-id-input">{"模型 ID\n                  "}<input id="manual-model-id-input" maxLength={128} placeholder="例如 gpt-5-mini" />
                </label>
                <p >{"手动兜底同样使用后端画像，不需要填写质量、价格或延迟。"}</p>
              </details>
              <div className="model-option-row">
                <label className="check-field"><input id="registered-model-enabled-input" type="checkbox" defaultChecked={true} />{" 启用"}</label>
                <label className="check-field"><input id="registered-model-auto-input" type="checkbox" defaultChecked={true} />{" 允许自动选择"}</label>
              </div>
              <div className="surface-actions">
                <p >{"模型输出统一使用普通 8,192 / thinking 与截断恢复 64,000 token；冷启动使用后端先验，产生真实请求后路由自动采用实测延迟。"}</p>
                <button id="save-registered-model-btn" className="button primary" type="button">{"注册所选模型"}</button>
              </div>
            </article>

            <article className="surface">
              <div className="surface-heading"><div ><span className="step-kicker">{"03 · 状态"}</span><h2 >{"已注册模型"}</h2></div><span id="registered-model-count" className="meta-badge">{"0"}</span></div>
              <div id="registered-model-list" className="registered-model-list"><ReactSlot name="models" /></div>
            </article>
          </div>
        </ViewPanel>);
});
