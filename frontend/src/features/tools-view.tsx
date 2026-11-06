import { EditorDialog } from '../components/EditorDialog';
import { SectionTabs } from '../components/SectionTabs';
import { memo } from 'react';
import { ReactSlot } from '../components/ReactSlot';
import { ViewPanel } from '../components/ViewPanel';

export const ToolsView = memo(function ToolsView() {
 return (<ViewPanel id="tools-view" aria-labelledby="tools-title" view="tools" className="view-panel">
          <header className="view-header compact">
            <div >

              <h1 id="tools-title">{"工具"}</h1>
              <p >{"统一管理全局 Skill 与 MCP Server；可用项会出现在任意 Workspace 的 "}<code >{"/"}</code>{" 菜单。"}</p>
            </div>
            <button id="refresh-tools-registry-btn" className="button secondary" type="button">{"刷新全部"}</button>
          </header>

          <SectionTabs id="tools" tabs={[
            { label: 'Skill', content: <>          <div id="skill-runtime-note" className="mcp-runtime-note" role="status" aria-live="polite" hidden={true}></div>

          <article className="surface tool-registry-intro">
            <div >

              <h2 >{"全局 Skill"}</h2>
              <p >{"目录 "}<code id="skill-registry-root">{"~/.cogent/skills"}</code>{" 对所有 Workspace 生效。显式 "}<code >{"/"}</code>{" 选择会直接加载；隐式匹配按需读取正文。"}</p>
            </div>
            <span id="skill-list-count" className="meta-badge">{"0"}</span>
          </article>

          <div className="content-grid mcp-registry-grid skill-registry-grid">
<EditorDialog id="skills-editor" title="添加 / 编辑 Skill">            <article className="surface mcp-registration-surface">
              <div className="surface-heading">
                <div ><h2 id="skill-form-title">{"添加 Skill"}</h2><p >{"使用兼容的 "}<code >{"SKILL.md"}</code>{" Frontmatter 与 Markdown 正文。"}</p></div>
                <button id="reset-skill-form-btn" className="text-button" type="button">{"清空"}</button>
              </div>
              <form id="skill-form" autoComplete="off">
                <label htmlFor="skill-name-input">{"Skill 名称\n                  "}<input id="skill-name-input" required={true} maxLength={64} pattern="[a-z0-9][a-z0-9-]{0,63}" placeholder="例如 code-review" spellCheck="false" />
                </label>
                <label htmlFor="skill-content-input">{"SKILL.md\n                  "}<textarea id="skill-content-input" className="skill-editor" required={true} rows={16} spellCheck="false" placeholder="---\nname: code-review\ndescription: Review code changes with focused findings.\nagents: [coding]\nmodes: [default]\ncommand:\n  name: code-review\n---\n\nReview the requested changes…"></textarea>
                </label>
                <label className="check-field"><input id="skill-enabled-input" type="checkbox" defaultChecked={true} />{" 启用并显示在 "}<code >{"/"}</code>{" 菜单"}</label>
                <div className="surface-actions">
                  <p id="skill-form-hint">{"保存后无需重启，所有 Workspace 立即使用同一份注册表。"}</p>
                  <button id="save-skill-btn" className="button primary" type="submit">{"保存 Skill"}</button>
                </div>
              </form>
            </article></EditorDialog>
            <article className="surface">
              <div className="surface-heading"><div ><h2 >{"已注册 Skill"}</h2><p >{"系统 Skill 只读，用户 Skill 可编辑和停用。"}</p></div></div>
              <div id="skill-list" className="mcp-server-list"><ReactSlot name="skills" /></div>
            </article>
          </div>

</> },
            { label: 'MCP', content: <>          <div className="tool-section-heading">
            <div ><h2 >{"MCP 连接"}</h2><p >{"注册外部 Server，验证连接状态，再把发现的工具交给 Agent。"}</p></div>
          </div>

          <div id="mcp-runtime-note" className="mcp-runtime-note" role="status" aria-live="polite" hidden={true}></div>

          <article className="surface mcp-topology" aria-label="MCP 连接概览">
            <div className="mcp-topology-track" aria-hidden="true">
              <span className="mcp-topology-node host">{"HOST"}</span>
              <i ></i>
              <span className="mcp-topology-node protocol">{"MCP"}</span>
              <i ></i>
              <span className="mcp-topology-node servers">{"SERVER"}</span>
            </div>
            <dl className="mcp-stat-grid">
              <div ><dt >{"已注册"}</dt><dd id="mcp-server-count">{"0"}</dd></div>
              <div ><dt >{"连接就绪"}</dt><dd id="mcp-ready-count">{"0"}</dd></div>
              <div ><dt >{"可用工具"}</dt><dd id="mcp-tool-count">{"0"}</dd></div>
            </dl>
          </article>

          <div className="content-grid mcp-registry-grid">
<EditorDialog id="mcp-editor" title="添加 / 编辑 MCP Server">            <article className="surface mcp-registration-surface">
              <div className="surface-heading">
                <div >

                  <h2 id="mcp-form-title">{"添加 Server"}</h2>
                  <p >{"凭据会写入 SecretStore；配置文件只保存引用。"}</p>
                </div>
                <button id="reset-mcp-form-btn" className="text-button" type="button">{"清空"}</button>
              </div>

              <form id="mcp-server-form" autoComplete="off">
                <div className="field-grid">
                  <label htmlFor="mcp-name-input">{"Server 名称\n                    "}<input id="mcp-name-input" required={true} maxLength={128} pattern="[A-Za-z0-9_.-]+" placeholder="例如 github" spellCheck="false" />
                  </label>
                  <label htmlFor="mcp-transport-input">{"传输方式\n                    "}<select id="mcp-transport-input">
                      <option value="stdio">{"stdio · 当前协议"}</option>
                      <option value="streamable_http">{"Streamable HTTP · 当前协议"}</option>
                      <option value="stdio_2025_06_18">{"stdio · 2025-06-18 兼容"}</option>
                      <option value="legacy_sse">{"HTTP+SSE · 旧版兼容"}</option>
                    </select>
                  </label>
                </div>

                <div id="mcp-stdio-fields" className="mcp-transport-fields">
                  <label htmlFor="mcp-command-input">{"启动命令\n                    "}<input id="mcp-command-input" maxLength={4096} placeholder="例如 npx" spellCheck="false" />
                  </label>
                  <label htmlFor="mcp-args-input">{"参数 · 每行一个\n                    "}<textarea id="mcp-args-input" rows={3} placeholder="-y\n@modelcontextprotocol/server-github" spellCheck="false"></textarea>
                  </label>
                  <label htmlFor="mcp-env-input">{"普通环境变量 · KEY=value\n                    "}<textarea id="mcp-env-input" rows={3} placeholder="LOG_LEVEL=info" spellCheck="false"></textarea>
                  </label>
                  <label htmlFor="mcp-env-secrets-input">{"Secret 环境变量 · KEY=value\n                    "}<textarea id="mcp-env-secrets-input" className="secret-entry" rows={3} placeholder="GITHUB_TOKEN=粘贴后只会安全保存" spellCheck="false"></textarea>
                    <small >{"编辑已有 Server 时，保留 "}<code >{"KEY="}</code>{" 即沿用原凭据。"}</small>
                  </label>
                </div>

                <div id="mcp-http-fields" className="mcp-transport-fields" hidden={true}>
                  <label htmlFor="mcp-url-input">{"Server URL\n                    "}<input id="mcp-url-input" type="url" maxLength={2048} placeholder="https://mcp.example.com/mcp" spellCheck="false" />
                  </label>
                  <label htmlFor="mcp-allowed-hosts-input">{"允许的 Host · 逗号或换行分隔\n                    "}<textarea id="mcp-allowed-hosts-input" rows={2} placeholder="mcp.example.com" spellCheck="false"></textarea>
                  </label>
                  <label htmlFor="mcp-headers-input">{"普通 Header · Name=value\n                    "}<textarea id="mcp-headers-input" rows={3} placeholder="X-Workspace=demo" spellCheck="false"></textarea>
                  </label>
                  <label htmlFor="mcp-header-secrets-input">{"Secret Header · Name=value\n                    "}<textarea id="mcp-header-secrets-input" className="secret-entry" rows={3} placeholder="Authorization=Bearer …" spellCheck="false"></textarea>
                    <small >{"认证 Header 不能作为普通配置保存。"}</small>
                  </label>
                </div>

                <div className="mcp-option-row">
                  <label className="check-field"><input id="mcp-enabled-input" type="checkbox" defaultChecked={true} />{" 启用连接"}</label>
                  <label className="check-field"><input id="mcp-required-input" type="checkbox" />{" 影响 readiness"}</label>
                </div>

                <details className="advanced-options mcp-advanced-options">
                  <summary >{"超时、重试与网络边界"}</summary>
                  <div className="field-grid mcp-number-grid">
                    <label htmlFor="mcp-connect-timeout-input">{"连接超时（秒）"}<input id="mcp-connect-timeout-input" type="number" min="0.1" max="300" step="0.1" defaultValue="10" /></label>
                    <label htmlFor="mcp-request-timeout-input">{"请求超时（秒）"}<input id="mcp-request-timeout-input" type="number" min="0.1" max="900" step="0.1" defaultValue="10" /></label>
                    <label htmlFor="mcp-max-retries-input">{"最大重试"}<input id="mcp-max-retries-input" type="number" min="0" max="10" step="1" defaultValue="1" /></label>
                    <label htmlFor="mcp-cache-ttl-input">{"工具缓存（秒）"}<input id="mcp-cache-ttl-input" type="number" min="0" max="86400" step="1" defaultValue="30" /></label>
                  </div>
                  <div className="mcp-option-row">
                    <label className="check-field"><input id="mcp-insecure-http-input" type="checkbox" />{" 允许明文 HTTP"}</label>
                    <label className="check-field"><input id="mcp-private-network-input" type="checkbox" />{" 允许私网地址"}</label>
                    <label className="check-field"><input id="mcp-legacy-input" type="checkbox" />{" 启用旧协议兼容"}</label>
                  </div>
                </details>

                <div className="surface-actions">
                  <p id="mcp-form-hint">{"保存后立即连接并发现工具；连接失败不会阻止其他 Server。"}</p>
                  <button id="save-mcp-server-btn" className="button primary" type="submit">{"保存并连接"}</button>
                </div>
              </form>
            </article></EditorDialog>

            <article className="surface">
              <div className="surface-heading">
                <div ><h2 >{"Server 与工具"}</h2><p >{"每条连接独立重试、熔断和关闭。"}</p></div>
                <span id="mcp-list-count" className="meta-badge">{"0"}</span>
              </div>
              <div id="mcp-server-list" className="mcp-server-list"><ReactSlot name="mcp" /></div>
            </article>
          </div>
</> },
          ]} />
        </ViewPanel>);
});
