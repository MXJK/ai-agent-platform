# MODEL-DISCOVERY-SELECTION：保留可用模型下拉选择

## Goal

修复模型管理页中“可用模型”下拉框在用户切换选项后立即跳回第一个模型的问题。

## In scope

- 保留仍然可注册的当前模型选择。
- Provider 切换、模型已注册或选择失效时，回退到第一个可注册模型。
- 补充前端回归测试，并在真实浏览器中验证智谱 GLM 与 MiniMax。

## Out of scope

- 不改变 Provider 模型发现接口、模型目录过滤或注册 API。
- 不修改会话首选模型选择器。
- 不提交、推送、部署、迁移数据库或调用付费生成模型。

## Acceptance criteria

- [x] GLM 与 MiniMax 的非首项模型可保持选中，不再跳回第一项。
- [x] 初次发现、切换 Provider 或原选择失效时仍自动选择第一个可注册模型。
- [x] 已注册模型继续不可选择；没有可注册模型时下拉框继续禁用。
- [x] 聚焦测试、静态检查、compileall 和真实浏览器验收通过。
- [x] 完整 pytest 通过，或精确记录与本任务无关的既有 blocker。

## Decisions

- 在重建 `<select>` 选项前保存当前值；若该模型仍在未注册的可选集合中则恢复它，否则选择第一个可注册模型。
- 保留既有的已注册模型禁用和空目录禁用语义，不改变发现或注册 API。
- 提升 `app.js` 静态资源版本号，避免已缓存页面继续执行旧版选择逻辑。

## Verification

- `node --test tests/test_model_config_dismiss.mjs`：`6 passed`。
- `node --test tests/test_chat_message_ui.mjs tests/test_model_config_dismiss.mjs`：`28 passed`。
- `.venv/bin/python -m pytest -q tests/test_model_registry.py tests/test_api.py`：`72 passed, 11 subtests passed`。
- 静态资源版本与注册页聚焦复跑：`2 passed`。
- `.venv/bin/python -m compileall ai_agent_platform tests evals`、`node --check ai_agent_platform/static/app.js`、`git diff --check`：通过。
- ego-browser 真实页面加载 `/static/app.js?v=20260907-model-selection-r1`：选择 `glm-4.5-air` 后仍为该值并展示对应摘要；切换 MiniMax 后选择 `MiniMax-M2.1` 也保持该值并展示对应摘要。
- `.venv/bin/python -m pytest -q`：`829 passed, 12 skipped, 108 subtests passed, 1 failed`。唯一失败仍为任务外既有的 `tests/test_cogent_tool_results.py::test_result_storage_never_follows_symlinked_parent`：测试期望 `OSError`，`ManagedFiles.read` 对 `ELOOP/ENOTDIR` 抛出 `ValueError`。

## Result

模型发现下拉框已能保留用户选择，GLM 与 MiniMax 的多模型目录均通过真实页面验收；失效选择和无可注册模型的回退语义有自动化覆盖。

本修复恢复选择器既有意图，没有改变 API、配置、架构或能力边界，因此不做 README / Interview Notes 的装饰性更新。仓库要求的完整 pytest 仍有上述任务外既有失败，工作流保持 `blocked`，且不记录未提交代码为已验证 commit。
