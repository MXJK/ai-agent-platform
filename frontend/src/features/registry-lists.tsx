import { useState } from 'react';
import { publishSlot } from '../components/ReactSlot';
import { EmptyState, Status } from '../components/Status';
import type { Skill, McpServer, ProviderConnection, RegisteredModel } from './registry-types';
import { mcpCardState, mcpStatusClass, mcpStatusLabel, mcpTransportLabel, handleSkillAction, handleMCPServerAction } from '../runtime/tools.js';
import { MODEL_PROVIDERS, modelStatusClass, modelStatusLabel, registeredModel } from '../runtime/models.js';
import { handleProviderAction, modelTierLabel, updateRegisteredModel, testRegisteredModel, deleteRegisteredModel } from '../runtime/model-actions.js';
import { formatDate, formatDuration } from '../runtime/format.js';
import { truncate } from '../runtime/feedback.js';

export function SkillList({ skills }: { skills: Skill[] }) {
  if (!skills.length) return <EmptyState>尚未注册 Skill。保存后会立即出现在所有 Workspace。</EmptyState>;
  return skills.map(skill => <article key={skill.name} className={`mcp-server-card ${skill.enabled ? 'ready' : 'disabled'}`} data-skill-name={skill.name}>
    <div className="mcp-server-heading">
      <div><strong>{skill.name}</strong><small>{skill.description}</small></div>
      <Status tone={skill.enabled ? 'ok' : 'neutral'}>{skill.enabled ? '已启用' : '已停用'}</Status>
    </div>
    <div className="mcp-server-meta"><span>/{skill.command?.name || skill.name}</span><span>{skill.source}</span><span>{skill.editable ? '全局用户目录' : '系统内置'}</span></div>
    <div className="mcp-tool-chips">{skill.required_tools?.length ? skill.required_tools.map(name => <code key={name}>{name}</code>) : <small>不声明额外工具</small>}</div>
    <div className="mcp-server-actions">{skill.editable ? <>
      <button className="button ghost" type="button" data-skill-action="edit" onClick={event => void handleSkillAction(event.currentTarget)}>编辑</button>
      <button className="button ghost" type="button" data-skill-action="toggle" onClick={event => void handleSkillAction(event.currentTarget)}>{skill.enabled ? '停用' : '启用'}</button>
      <button className="text-button danger" type="button" data-skill-action="delete" onClick={event => void handleSkillAction(event.currentTarget)}>删除</button>
    </> : <span className="meta-badge">只读</span>}</div>
  </article>);
}

export function McpList({ servers, runtimeEnabled }: { servers: McpServer[]; runtimeEnabled: boolean }) {
  if (!servers.length) return <EmptyState>尚未注册 MCP Server。填写左侧配置即可开始发现工具。</EmptyState>;
  return servers.map(server => {
    const tools = server.registered_tools || [];
    return <article key={server.name} className={`mcp-server-card ${mcpCardState(server.state)}`} data-mcp-server={server.name}>
      <div className="mcp-server-heading"><div><strong>{server.name}</strong><small title={server.endpoint}>{server.endpoint}</small></div><Status tone={mcpStatusClass(server.state)}>{mcpStatusLabel(server.state)}</Status></div>
      <div className="mcp-server-meta"><span>{mcpTransportLabel(server.transport)}</span><span>{server.required ? 'required' : 'optional'}</span><span>{server.protocol_version || '协议待协商'}</span><span>{server.discovered_tools?.length || 0} 已发现 · {tools.length} 已注册</span>{server.cache_hit && <span>缓存命中</span>}</div>
      {server.last_error_code && <p className="mcp-server-error">错误码：{server.last_error_code} · 重试 {server.retry_count}</p>}
      <div className="mcp-tool-chips">{tools.length ? tools.slice(0, 8).map(name => <code key={name}>{name}</code>) : <small>尚未注册工具</small>}{tools.length > 8 && <span>+{tools.length - 8}</span>}</div>
      <div className="mcp-server-actions">{(['edit', 'test', 'toggle', 'delete'] as const).map(action => <button key={action} type="button" data-mcp-action={action}
        className={action === 'delete' ? 'text-button danger' : 'button ghost'}
        disabled={action === 'test' && (!runtimeEnabled || !server.enabled)}
        onClick={event => void handleMCPServerAction(event.currentTarget)}>
        {{ edit: '编辑', test: '测试 / 刷新', toggle: server.enabled ? '停用' : '启用', delete: '删除' }[action]}
      </button>)}</div>
    </article>;
  });
}

function ProviderCard({ provider, displayName, connection }: { provider: string; displayName: string; connection?: ProviderConnection }) {
  const configured = connection?.credential_configured === true;
  const status = connection?.status || 'unavailable';
  const [apiKey, setApiKey] = useState('');
  const [enabledDraft, setEnabledDraft] = useState<boolean | null>(null);
  return <article className="provider-card" data-provider={provider}>
    <div className="provider-card-heading"><div><strong>{displayName}</strong><small>{provider}</small></div><Status tone={modelStatusClass(status)}>{modelStatusLabel(status)}</Status></div>
    <label>API Key<input data-provider-key type="password" autoComplete="new-password" value={apiKey} onChange={event => setApiKey(event.target.value)} placeholder={configured ? '已安全保存；输入新值可替换' : '输入 API Key'} /></label>
    <label className="check-field"><input data-provider-enabled type="checkbox" checked={enabledDraft ?? connection?.enabled !== false} onChange={event => setEnabledDraft(event.target.checked)} /> 启用连接</label>
    <div className="button-row"><button className="button secondary" type="button" data-provider-action="save" onClick={async event => {
      // Only clear a credential draft after a successful save, never on telemetry refresh.
      if (await handleProviderAction(event.currentTarget)) { setApiKey(''); setEnabledDraft(null); }
    }}>{configured ? '更新配置' : '保存配置'}</button>
      <button className="button ghost" type="button" data-provider-action="test" disabled={!connection} onClick={event => void handleProviderAction(event.currentTarget)}>测试连接</button></div>
    <p>{connection?.credential_error || (configured ? '凭证已安全保存' : '尚未配置凭证，请重新输入 API Key')} · {connection?.model_count || 0} 个模型</p>
  </article>;
}

export function ProviderList({ connections }: { connections: ProviderConnection[] }) {
  return MODEL_PROVIDERS.map(([provider, displayName]) => <ProviderCard key={provider} provider={provider} displayName={displayName} connection={connections.find(item => item.provider === provider)} />);
}

export function ModelList({ models }: { models: RegisteredModel[] }) {
  if (!models.length) return <EmptyState>保存 Provider 后，在左侧注册第一个模型。</EmptyState>;
  return models.map(model => {
    const telemetry = model.telemetry || {};
    const probe = telemetry.probe || {};
    const routing = model.routing_metadata || {};
    const latency = routing.latency_source === 'observed_p50'
      ? `实测 P50 ${formatDuration(routing.routing_latency_ms)} · P95 ${formatDuration(telemetry.total_latency_p95_ms)}`
      : `冷启动先验 ${formatDuration(routing.routing_latency_ms || model.configured_latency_ms)}`;
    const failed = probe.last_failure_at && (!probe.last_success_at || new Date(probe.last_failure_at) > new Date(probe.last_success_at));
    const probeLatency = failed ? `探测失败 · ${truncate(probe.last_error, 48)}` : probe.success_count
      ? `固定探测 ${formatDuration(probe.last_latency_ms)} · P50 ${formatDuration(probe.latency_p50_ms)}` : '尚未手动测速';
    const latestUpdate = [probe.updated_at, telemetry.updated_at].filter((value): value is string => Boolean(value)).sort((a, b) => Date.parse(b) - Date.parse(a))[0];
    return <article key={model.id} className="registered-model-card" data-model-id={model.id}>
      <div className="registered-model-heading"><div><strong>{model.display_name}</strong><small>{model.provider} · {model.model}</small></div><Status tone={modelStatusClass(model.status)}>{modelStatusLabel(model.status)}</Status></div>
      <div className="model-stat-row"><span>{latency} · {Number(telemetry.sample_count || 0).toLocaleString()} 个业务样本</span><span>{probeLatency} · {Number(probe.sample_count || 0).toLocaleString()} 次</span><span>{model.context_window_tokens.toLocaleString()} ctx</span><span>8,192 普通 / {model.max_output_tokens.toLocaleString()} thinking 与恢复</span></div>
      <p>{modelTierLabel(routing.quality_tier)} · {modelTierLabel(routing.cost_tier)} · {model.auto_eligible ? '可自动选择' : '仅手动选择'}{model.enabled ? '' : ' · 已停用'} · 最近更新 {latestUpdate ? formatDate(latestUpdate) : '暂无样本'}{telemetry.last_error ? ` · ${truncate(telemetry.last_error, 80)}` : ''}</p>
      <div className="button-row"><button className="button secondary" type="button" data-model-action="test-latency" disabled={!model.enabled} aria-label={`测试 ${model.display_name} 延迟`} onClick={event => void testRegisteredModel(model.id, event.currentTarget)}>测试延迟</button>
        <button className="button ghost" type="button" data-model-action="toggle-enabled" onClick={() => void updateRegisteredModel(model.id, { enabled: !model.enabled })}>{model.enabled ? '停用' : '启用'}</button>
        <button className="button ghost" type="button" data-model-action="toggle-auto" onClick={() => void updateRegisteredModel(model.id, { auto_eligible: !model.auto_eligible })}>{model.auto_eligible ? '仅手动' : '加入自动'}</button>
        <button className="button ghost" type="button" data-model-action="delete" onClick={() => void deleteRegisteredModel(model.id)}>删除</button></div>
    </article>;
  });
}

export const renderSkills = (skills: Skill[]) => publishSlot('skills', <SkillList skills={skills} />);
export const renderMcpServers = (servers: McpServer[], runtimeEnabled: boolean) => publishSlot('mcp', <McpList servers={servers} runtimeEnabled={runtimeEnabled} />);
export const renderProviders = (connections: ProviderConnection[]) => publishSlot('providers', <ProviderList connections={connections} />);
export const renderModels = (models: RegisteredModel[]) => publishSlot('models', <ModelList models={models} />);
