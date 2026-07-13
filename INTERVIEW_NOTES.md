# AI Agent Platform Interview Notes

这个文档用于长期记录本项目的工程知识点、面试表达方式和开发过程中遇到的问题。后续每增加一个模块、接口、架构调整或线上化能力，都可以继续追加到这里。

## 目录

- 项目架构与分层
- LLM API 工程化
- SSE 流式聊天接口
- RAG 最小可用系统
- Embedding、向量库、召回与重排
- RAG Prompt 和引用片段
- 上下文限制
- 超时、重试和错误处理
- 日志与 token 用量
- 开发问题记录
- 面试高频问题

## 项目架构与分层

当前项目采用轻量分层结构：

- `api`：HTTP 路由层，负责请求校验、响应协议和 HTTP 错误。
- `schemas`：Pydantic 模型层，负责 API 入参和出参结构。
- `services`：应用服务层，负责会话、消息和业务用例编排。
- `repositories`：数据访问边界，当前是内存实现，后续可替换为数据库。
- `integrations`：外部系统集成边界，例如 LLM、RAG、工具调用。
- `domain`：业务实体，不依赖 FastAPI、数据库或外部 SDK。

面试表达：

> 这个项目不是把所有逻辑塞进 FastAPI route，而是把 HTTP 协议、业务用例、存储边界和外部 provider 隔离开。这样后续替换数据库、替换 LLM provider 或增加测试都会更容易。

## LLM API 工程化

LLM API 工程化关注的不只是“能不能调通模型”，还包括：

- API key 和模型配置。
- 上下文窗口和 token 预算。
- 流式输出协议。
- 超时和重试策略。
- provider 错误归一化。
- 日志、成本和 token 用量记录。
- 测试时如何 mock 外部模型。

## SSE 流式聊天接口

本项目当前的聊天流式接口是 `POST /api/v1/chat/stream`，请求体包含 `conversation_id` 和 `message`。

## 接口调用链

```text
client
  -> POST /api/v1/chat/stream
  -> api/router.py 校验 conversation_id 和输入长度
  -> SessionService.build_chat_context() 组装上下文
  -> SessionService.add_message(role="user") 记录用户消息
  -> LLMClient.stream_chat() 调用 fake/openai/anthropic provider
  -> StreamingResponse 持续返回 SSE event
  -> 流结束后记录 assistant 消息和 token usage
```

面试表达：

> 我把 HTTP 传输层、业务会话层和外部 LLM provider 层分开。路由层负责 SSE 协议和 HTTP 错误，service 层负责会话和消息状态，integration 层负责外部 API 的超时、重试、SSE 解析和错误归一化。

## SSE 为什么适合聊天

SSE 是基于 HTTP 的服务端单向事件流，浏览器或前端客户端建立连接后，服务端可以持续推送事件。聊天场景里，用户发出一次输入后，主要是服务端持续返回模型 token，因此 SSE 比普通 JSON 响应更适合逐字展示，也比 WebSocket 更简单。

适合 SSE 的原因：

- 模型输出是单向流：服务端不断推送 delta，前端追加显示。
- 基于 HTTP：容易经过网关、日志系统和鉴权中间件。
- 事件结构清晰：可以区分 `delta`、`usage`、`done`、`error`。
- 前端体验好：不用等完整回复生成完，首 token 到达后即可显示。

不适合 SSE 的场景：

- 前端和后端需要高频双向通信。
- 需要客户端在同一条连接里不断追加新输入。
- 需要复杂实时协作或游戏同步，这时 WebSocket 更合适。

## 本项目的 SSE 事件设计

```text
event: meta
data: {"request_id":"chat_xxx","provider":"fake","model":"demo-stream-model"}

event: delta
data: {"text":"hello "}

event: usage
data: {"input_tokens":10,"output_tokens":20,"total_tokens":30}

event: done
data: {"request_id":"chat_xxx","elapsed_ms":123,"input_tokens":10,"output_tokens":20}

event: error
data: {"code":"llm_provider_error","message":"...","retryable":true}
```

面试表达：

> 我没有把 OpenAI 或 Claude 的原始事件直接透传给前端，而是转换成业务稳定的事件协议。这样以后替换 provider，不会影响前端消费逻辑。

## 上下文限制

LLM 有上下文窗口限制，工程里不能无限把历史消息塞给模型。本项目先用两个简单限制：

- `llm_max_input_chars`：限制单次用户输入长度，超过后返回 413。
- `llm_max_context_messages`：只取最近 N 条历史消息组装上下文。

真实生产系统还会做：

- 使用 tokenizer 计算真实 token 数。
- 对旧消息做摘要压缩。
- 按角色、时间、重要性裁剪上下文。
- 将 RAG 检索结果和聊天历史分别预算 token。

## 超时和重试

外部 LLM API 可能出现网络慢、429 限流、5xx、连接中断等问题。本项目在 `LLMClient` 里处理：

- `httpx.Timeout(settings.llm_timeout_seconds)` 控制请求超时。
- 408、409、429、5xx 视为可重试错误。
- 使用指数退避重试。
- 如果已经向前端输出过 token，就不再重试，避免重复内容。

面试表达：

> 流式接口的重试和普通接口不同。如果模型已经输出了一部分 token，再重试可能导致前端看到重复文本，所以我只在还没有 emitted delta 前做重试。

## 错误处理

普通 JSON 接口可以直接返回 HTTP 4xx/5xx；但 SSE 连接建立后，再发生错误时不能重新设置 HTTP status。本项目策略：

- 连接建立前的错误：比如 conversation 不存在，直接返回 404。
- 连接建立后的 provider 错误：返回 `event: error`。
- 未知异常：记录日志，返回统一 `internal_error` SSE。

面试表达：

> SSE 的错误边界分两层：stream 开始前还能用 HTTP status，stream 开始后只能通过事件协议告诉前端错误。

## 日志和 Token 用量

本项目记录：

- `request_id`
- `conversation_id`
- `provider`
- `model`
- `elapsed_ms`
- `input_tokens`
- `output_tokens`

token usage 被保存到内存 repository，后续可以换成数据库表。真实系统里这些数据用于：

- 成本统计
- 限流和配额
- 慢请求分析
- 用户级账单
- provider 质量监控

## OpenAI 和 Claude 流式差异

OpenAI Responses API 使用 `stream=True` 后返回 SSE 语义事件，例如文本 delta 和 completed 事件。Claude Messages API 也返回 SSE，但事件类型是 `message_start`、`content_block_delta`、`message_delta`、`message_stop` 等。

本项目做了 provider 归一化：

```text
OpenAI response.output_text.delta -> delta
OpenAI response.completed         -> usage/done

Claude content_block_delta text_delta -> delta
Claude message_delta usage            -> usage
Claude message_stop                   -> done
```

## RAG 最小可用系统

RAG 是 Retrieval-Augmented Generation，即检索增强生成。它不是把文档训练进模型，而是在用户提问时先从知识库检索相关片段，再把片段拼进 prompt，让 LLM 基于这些资料回答。

本项目已经实现一条最小可用 RAG 链路：

```text
上传文档
  -> 文本解析
  -> chunking 切块
  -> embedding 生成向量
  -> 向量库存储
  -> 用户提问
  -> query embedding
  -> 向量召回候选 chunk
  -> 可选 reranker 重排
  -> top chunks 拼进 prompt
  -> LLMClient 生成答案
  -> 返回 answer + citations
```

相关文件：

- `ai_agent_platform/integrations/rag.py`：RAG 核心流程，包括 parser、chunker、embedding provider、vector store、reranker、prompt 组装。
- `ai_agent_platform/schemas/rag.py`：RAG API 的请求和响应结构。
- `ai_agent_platform/api/router.py`：知识库文档入库、搜索、问答接口。
- `ai_agent_platform/core/config.py`：RAG 配置项。

对外 API：

```text
POST /api/v1/knowledge-bases/{knowledge_base_id}/documents
POST /api/v1/knowledge-bases/{knowledge_base_id}/search
POST /api/v1/knowledge-bases/{knowledge_base_id}/ask
```

面试表达：

> 我把 RAG 拆成文档解析、切块、embedding、向量存储、召回、重排、prompt 组装和 LLM 调用几个边界。这样后续替换向量库、替换 embedding 模型、增加 PDF parser 或增加 reranker，都不需要重写 API 层。

## 文档解析和 Chunking

当前第一版支持 `.txt`、`.md`、`.markdown`。生产系统通常还需要支持 PDF、Word、HTML、网页、OCR、表格和图片。

chunking 使用 `RecursiveCharacterChunker`：

```text
chunk_size = 800
chunk_overlap = 120
```

切块过程：

```text
从 start=0 开始
取最多 800 个字符
优先在自然边界截断：
  1. 空行
  2. 换行
  3. 中文句号
  4. 英文句号
  5. 空格
如果找不到合适边界，就硬切
下一块从 end - 120 开始，保留 overlap
```

为什么需要 overlap：

- 避免把一句规则或一个概念拆断。
- 让下一块保留上一块末尾的上下文。
- 减少检索命中后片段信息不完整的问题。

真实业务坑：

- chunk 太大：检索结果不精准，prompt 空间浪费。
- chunk 太小：上下文丢失，模型拿到的信息不完整。
- 固定长度硬切：容易切断表格、标题、步骤说明和制度条款。
- 文档格式复杂：PDF 页眉页脚、表格、目录、脚注会污染 chunk。

面试表达：

> chunking 不是简单按长度切字符串。我会优先按自然语义边界切，比如段落、句子、标题，并保留 overlap。这样既能控制 token 成本，又能减少上下文断裂。

## Embedding 选型

本项目当前默认使用 Google Gemini `gemini-embedding-001`：

```bash
export EMBEDDING_PROVIDER=gemini
export EMBEDDING_MODEL=gemini-embedding-001
export GOOGLE_API_KEY=...
```

选择理由：

- API 接入简单，适合学习和第一版业务 RAG。
- 不需要本地下载模型或处理显存问题。
- 可以复用 Google/Gemini 体系里的 API key 和模型服务。
- 与 Chroma、pgvector 等向量库存储都容易集成。
- 支持按检索用途区分 `RETRIEVAL_DOCUMENT` 和 `RETRIEVAL_QUERY`，适合 RAG 入库和查询场景。

项目里仍保留 `local` hash embedding provider，但它只适合测试和离线教学：

```text
文本 -> token -> sha256 hash -> 映射到固定维度向量 -> L2 normalize
```

它不是真正的语义 embedding，不能很好处理“退款”和“退钱”这类语义相近但字面不同的问题。因此真实业务不应该依赖它。

真实业务可选模型：

- `gemini-embedding-001`：当前项目默认选择，适合使用 Google Gemini 体系的 RAG。
- `text-embedding-3-small`：OpenAI 体系里的性价比选择。
- `text-embedding-3-large`：质量更高，但向量维度、存储和成本更高。
- `BAAI/bge-m3`：适合私有化、本地部署、多语言和长文档场景。
- `multilingual-e5-large-instruct`：适合 sentence-transformers 生态和多语言检索。

面试表达：

> embedding 模型必须在入库和查询时保持一致。文档入库用一个模型，查询用另一个模型，会导致向量空间不一致，检索质量会明显下降。

## 向量库和 Knowledge Base 隔离

本项目支持两种向量存储：

- `memory`：默认内存向量库，适合测试和学习。进程重启后数据丢失。
- `chroma`：本地持久化向量库，适合原型和 demo。

配置 Chroma：

```bash
export RAG_VECTOR_STORE=chroma
export CHROMA_PERSIST_DIRECTORY=.chroma
```

每个 chunk 都带有：

```text
knowledge_base_id
document_id
filename
chunk_index
text
embedding
```

检索时会先按 `knowledge_base_id` 过滤，再做相似度排序。这一点很重要，因为同一个 RAG 后端可能服务多个业务场景：

```text
customer_faq
product_docs
hr_policy
project_alpha
tech_docs
```

真实业务坑：

- 如果不按知识库或租户隔离，客服问答可能检索到 HR 制度或内部项目资料。
- 向量库里只存 text 不存 metadata，会导致无法溯源、无法权限过滤、无法删除指定文档。
- 只做向量相似度不做权限过滤，容易产生数据泄露。

面试表达：

> 多知识库 RAG 的关键不是只把文档放进一个向量库，而是每条 chunk 都携带 metadata，并在检索阶段先做业务隔离和权限过滤，再做向量相似度搜索。

## 召回和重排

项目最初是一阶段检索：

```text
query embedding -> vector search topK -> 直接进 prompt
```

现在已拆成两阶段：

```text
query embedding
  -> 向量库召回 recall_limit 个候选
  -> reranker 对候选重新打分
  -> 取最终 limit 个 chunk
  -> 拼进 prompt
```

API 参数：

```text
recall_limit = 第一阶段粗召回多少候选
limit        = 最终返回/喂给 LLM 多少片段
```

推荐范围：

```text
recall_limit = 20 或 50
limit = 3 到 8
```

项目支持 `sentence-transformers` 的 cross-encoder reranker：

```bash
export RAG_RERANKER_PROVIDER=sentence_transformer
export SENTENCE_TRANSFORMER_RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
```

召回和重排的区别：

- 召回：用 embedding + 向量库快速找一批可能相关的候选，目标是不要漏掉答案。
- 重排：用更精细的模型重新判断 query 和 chunk 的相关性，目标是把最有用的片段排到前面。

真实业务坑：

- 只召回不重排：topK 里可能有字面相似但回答不了问题的片段。
- 召回数量太小：正确答案没有进入候选集，重排也救不回来。
- 重排模型太大：延迟高、吞吐低，不适合高并发接口。
- 中英文或领域语料不匹配：reranker 分数可能不稳定，需要评测集验证。

面试表达：

> 我会把检索拆成 recall 和 rerank。向量库负责高召回和低延迟，reranker 负责精排质量。这样比直接 topK 进 prompt 更稳定，也更接近真实 RAG 系统。

## RAG Prompt 和引用片段

`/ask` 接口不是直接把用户问题发给 LLM，而是先检索知识库片段，再构造 prompt：

```text
system:
你是企业知识库问答助手，只能基于参考资料回答。
如果资料不足，就明确说不知道。

user:
用户问题：
...

参考资料：
[1] file=refund.md; document_id=...; chunk=0; score=...
...

请给出简洁答案，并在相关句子后标注引用编号，例如 [1]。
```

返回结构包含：

```text
answer
citations
```

`citations` 里包含 chunk 文本、文件名、document_id、chunk_index、score、recall_score、rerank_score。这样前端可以展示“答案依据来自哪里”。

真实业务坑：

- 只返回答案不返回引用，用户无法信任或核查。
- prompt 中塞太多 chunk，会浪费 token，并可能让模型注意力分散。
- 检索结果不足时，模型容易幻觉，因此 system prompt 要明确要求“不知道就说不知道”。
- 引用编号如果和 citations 顺序不一致，前端展示会混乱。

面试表达：

> RAG 的产物不应该只有 answer，还应该返回 citations。企业知识库场景里，可追溯性和可审计性很重要，用户需要知道答案来自哪个文档、哪个片段。

## RAG 和原 LLM 接口的关系

本项目的 RAG 没有重新写一个 LLM 调用器，而是复用原来的 `LLMClient.stream_chat()`。

也就是说：

```text
普通聊天：
用户消息 -> LLMClient

RAG 问答：
用户问题 -> RAG 检索 -> RAG prompt -> LLMClient
```

这样做的好处：

- OpenAI、Anthropic、fake provider 的调用逻辑仍然集中在 `LLMClient`。
- 超时、重试、SSE 解析和 provider 错误归一化不用重复实现。
- RAG 只负责检索增强，不负责底层模型供应商细节。

面试表达：

> 我没有让 RAG 模块直接调用 OpenAI chat 接口，而是复用已有 LLMClient。RAG 只负责把知识库上下文组装进 messages，真正的模型调用仍然走统一 provider 边界。

## 开发问题记录

### 1. 项目还不是 Git 仓库

现象：`git status` 提示 `not a git repository`。

处理：执行 `git init`，并新增 `.gitignore`。

### 2. 系统 Python 没安装 FastAPI

现象：用 `python3 -m unittest` 报 `ModuleNotFoundError: No module named 'fastapi'`。

处理：使用项目 `.venv/bin/python -m unittest discover -s tests`，与 README 的 venv 方式保持一致。

### 3. Python 3.9 类型语法兼容

现象：`str | None` 或 `Literal[...] | None` 在 Python 3.9/Pydantic 求值时失败。

处理：

- 普通模块加 `from __future__ import annotations`。
- Pydantic 字段使用 `Optional[...]`，避免运行时求值失败。

### 4. Starlette 413 状态码常量改名

现象：测试通过但出现 deprecation warning。

处理：路由里直接使用数字状态码 `413`，减少版本差异噪音。

## 面试高频问题

### Q1: SSE 和 WebSocket 怎么选？

如果是聊天输出这种服务端单向持续推送，优先 SSE；如果需要同一条连接上的高频双向通信，选择 WebSocket。

### Q2: 为什么不能直接把 provider 的原始事件返回给前端？

因为 provider 的事件格式会变化，OpenAI 和 Claude 也不一样。后端应该提供稳定的业务事件协议，隔离供应商差异。

### Q3: 流式输出中途失败怎么办？

连接建立前返回 HTTP 错误；连接建立后返回 `event: error`。前端收到 error 后停止追加文本，并给用户展示可重试状态。

### Q4: 流式接口如何记录 assistant 消息？

边流式输出边把 delta 追加到内存 buffer。流结束后，将完整 assistant 回复落库。如果中途失败，可以选择不落库、落 partial，或增加 message status 字段。

### Q5: token usage 为什么重要？

token usage 是 LLM 应用的成本、限流、计费和监控基础。没有 usage 记录，就很难判断哪个用户、哪个会话、哪个模型消耗最大。
