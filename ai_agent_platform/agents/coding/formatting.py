"""Human-readable answer formatting for coding-agent results."""

from __future__ import annotations

from ai_agent_platform.agents.coding.models import (
    CODING_AGENT_OBJECTIVE,
    CODING_AGENT_ROLE,
    CodingAgentState,
)
from ai_agent_platform.agents.coding.text import snippet
from ai_agent_platform.integrations.rag import RetrievedDocument


def format_answer(state: CodingAgentState) -> str:
    lines = [
        f"我是{CODING_AGENT_ROLE}。",
        f"目标：{CODING_AGENT_OBJECTIVE}",
        (
            f"我把本轮请求归类为 `{state.get('intent', 'repository_question')}`，"
            f"原因：{state.get('intent_reason', '')}。"
        ),
        f"仓库索引：`{state['repository_id']}`。",
    ]
    lines.append(f"已读取 {len(state.get('history', []))} 条历史消息作为上下文。")

    review_decision = state.get("review_decision", {})
    if review_decision and not review_decision.get("approved"):
        feedback = review_decision.get("feedback") or "未提供补充说明"
        lines.append("人工审批结果：未批准执行本轮需要权限确认的工具计划。")
        lines.append(f"审批反馈：{feedback}")
        lines.append("我已停止执行后续工具；可以根据反馈调整目标后重新发起 run。")
        return "\n".join(lines)

    repair_review_decision = state.get("repair_review_decision", {})
    if repair_review_decision and not repair_review_decision.get("approved"):
        feedback = repair_review_decision.get("feedback") or "未提供补充说明"
        lines.append("测试失败后的修复方案未获批准，已停止第二次代码修改。")
        lines.append(f"修复审批反馈：{feedback}")

    citations = state.get("rag_context", [])
    if citations:
        lines.append("我检索到的代码上下文：")
        for index, citation in enumerate(citations, start=1):
            lines.append(
                f"[{index}] {citation.filename} chunk={citation.chunk_index} "
                f"{citation_line_range(citation)}{citation_symbols(citation)}"
                f"score={citation.score:.3f}: {snippet(citation.text, limit=140)}"
            )
    else:
        lines.append(
            "我还没有检索到代码片段。可以先把 README、关键源码文件或目录索引写入这个 repository_id。"
        )

    tool_results = state.get("tool_results", [])
    if tool_results:
        lines.append("工具复盘：")
        for item in tool_results:
            lines.append(_tool_result_summary(item))

    change_status = state.get("change_status")
    if change_status:
        lines.append(
            "代码修改闭环："
            f"status={change_status}，"
            f"iterations={state.get('change_iteration', 0)}，"
            f"changed_files={state.get('changed_files', [])}。"
        )
        lines.append(
            "变更产物已写入 `artifacts`，"
            "其中包含测试报告和可人工审查的统一 Diff。"
        )

    if citations:
        lines.append(
            "回答建议：优先依据上面的文件片段定位实现；如果要改代码，下一步应沿 trace 中的候选文件做最小修改并补测试。"
        )
    else:
        lines.append(
            "下一步建议：先通过知识库 ingest 接口索引仓库文件，再用同一个 repository_id 继续提问。"
        )
    return "\n".join(lines)


def format_error_answer(state: CodingAgentState) -> str:
    errors = [
        error
        for error in state.get("errors", [])
        if not error.get("recovered", False)
    ] or state.get("errors", [])
    lines = [
        f"我是{CODING_AGENT_ROLE}。",
        "本轮 Agent 运行进入错误分支，未继续执行后续正常节点。",
        f"仓库索引：`{state['repository_id']}`。",
    ]
    if errors:
        lines.append("结构化错误：")
        for index, error in enumerate(errors, start=1):
            retry_text = "可重试" if error.get("retryable") else "不可重试"
            recovered_text = "已恢复" if error.get("recovered") else "未恢复"
            lines.append(
                f"[{index}] node={error.get('node')} code={error.get('code')} "
                f"attempt={error.get('attempt')}/{error.get('max_attempts')} "
                f"{retry_text} {recovered_text}: {error.get('message')}"
            )
    else:
        lines.append("结构化错误为空，但 graph 已切换到错误回答分支。")
    lines.append(
        "下一步建议：先根据 `errors` 中的 node/code 定位失败边界；"
        "如果是 provider/network 类错误可以重试，如果是 configuration/validation 类错误应先修配置或输入。"
    )
    return "\n".join(lines)


def citation_line_range(citation: RetrievedDocument) -> str:
    if citation.start_line is None or citation.end_line is None:
        return ""
    return f"lines={citation.start_line}-{citation.end_line} "


def citation_symbols(citation: RetrievedDocument) -> str:
    if not citation.symbols:
        return ""
    return "symbols=" + ",".join(citation.symbols[:3]) + " "


def _tool_result_summary(item: dict[str, object]) -> str:
    name = str(item.get("name", "unknown"))
    if not item.get("ok"):
        return f"- {name}: failed, {item.get('error')}"
    result = item.get("result")
    if name == "sandbox.run_command" and isinstance(result, dict):
        return (
            f"- {name}: exit_code={result.get('exit_code')} "
            f"command={result.get('command')}"
        )
    if name == "sandbox.git_diff" and isinstance(result, dict):
        return (
            f"- {name}: changed_files={result.get('changed_files', [])} "
            f"truncated={result.get('truncated', False)}"
        )
    if name == "sandbox.workspace_status" and isinstance(result, dict):
        return f"- {name}: changed_files={result.get('changed_files', [])}"
    return f"- {name}: {result}"
