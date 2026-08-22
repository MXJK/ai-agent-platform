@AGENTS.md

## Claude Code 专属约束

### 修改边界

- 只修改任务明确要求的文件。需要改动其他文件时，先停下来说明原因并等确认。
- 修改任何函数或类之前，先 grep 出全部调用方并列出来；调用方超过 3 处时先进入 plan mode。
- 不改动与任务无关的格式、命名、import 顺序、类型注解。
- 不新增第三方依赖，不修改公共函数签名与 API 契约，除非任务明确要求。

### 改前改后

- 重构既有行为前，先补 characterization 测试锁住当前行为，
  参照 `tests/test_agent_loop_characterization.py`。
- 每个任务开始前确认工作区干净；不要把新改动叠加在未提交的旧改动上。
- 结束前如实报告：测试失败就贴输出，跳过的步骤要说明。

### 本地运行环境

- `docker-compose.override.yml` 把 `./ai_agent_platform` 只读挂进 app 容器并设
  `APP_RELOAD=1`，因此 Python 与 static 改动会自动热重载，**不需要重建镜像**。
- `docker compose restart app` 不会加载新代码：基础镜像用 `COPY` 把源码烤进镜像，
  restart 只是重启同一个旧镜像。需要重建时用：

  ```bash
  docker compose up -d --build app
  ```

- 只有改了依赖、`Dockerfile`、`migrations/`，或提交前要验证真实镜像时才需要重建。

### 自动校验

`.claude/hooks/` 已配置两个 hook，无需手动调用：

- 每次 Edit/Write 之后对 `.py` 做语法检查。
- 回答结束前，若本轮改过 Python 就跑 `.venv/bin/python -m pytest -q`；
  若本轮改过 `ai_agent_platform/` 就确认 app 容器热重载后仍然健康。
  任一失败都会阻止我结束回答。
