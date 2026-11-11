# 调研记录与取舍

> 调研时间：2026-11-11。目标是找到「主动感知 + 任务中枢 + Agent 执行 + 人工确认」的可复用方案，并判断哪些能直接落地到这台 Windows 机器上。

## 1. 参考项目

### Multica（multica-ai/multica）

定位：开源的 "agent-native" 任务管理平台。核心抽象是把 **Issue 分配给 Agent**，由服务端 Runtime 启动 Claude Code / Codex 等 CLI 进程执行，人在看板上审查结果；支持运行时状态、任务日志、工作区隔离。

借鉴：

- **Issue 即上下文**：任务描述不是孤立的 prompt，而是携带项目背景、历史讨论的完整上下文包。
- **Runtime 托管执行**：Agent 不是一次性调用，而是由调度器启动、监控、回收的子进程。
- **人审门禁**：Agent 完成后结果进入 review 状态，人决定接受或打回。
- **Agent 作为队友**：绑定固定的 agent 配置（模型、工具、工作目录），而不是每次临时拼参数。

未采用：它依赖 Docker + Node.js 服务端 + PostgreSQL，本机没有 Docker，且部署维护成本高；其 UI 面向团队协作，对个人科研场景过重。

### agent-inbox（langchain-ai/agent-inbox）

定位：基于 LangGraph `interrupt` 的人机协作收件箱，展示 Agent 卡在哪一步，支持 accept / edit / respond / ignore。

借鉴：

- **Outbox 的交互范式**：把「待人类处理」当成一等公民，而不是塞在日志里。
- **四种处置动作**：本次实现为 approve / reject（+ 备注），复杂编辑留作扩展。
- **中断即状态**：任务不是失败，而是 `needs_approval`，恢复后继续。

未采用：绑定 LangGraph/LangChain 运行时与外部 SaaS 依赖，和本机已有的 CLI Agent 体系不匹配。

### 其他调研对象

| 项目/方案 | 结论 |
| --- | --- |
| OpenHands / SWE-agent | 强在仓库内自主修 bug；作为 worker 太重，且自带的 runtime 与我们已有的 Claude Code / Codex 重复 |
| AutoGen / CrewAI | 多 Agent 编排框架，适合原型；持久化、权限、人工门禁需要自己补，直接用自己的调度器更可控 |
| Temporal / Windmill | 工作流引擎，可靠但需要服务端与数据库，违背「便携、单机、标准库」约束 |
| n8n / Activepieces | 集成能力强，但以 SaaS 连接器为中心，本地文件 / 学术工具 / CLI Agent 反而要自己写节点 |
| Task Master AI | 任务拆解思路（任务 + 子任务 + 依赖）可借鉴，已吸收进 Planner 的 DAG 设计 |
| LangGraph interrupt | HITL 语义参考，落地为 approvals 表 + Outbox 页面 |

## 2. 本机能力盘点（调研结论）

| 能力 | 实际探测结果 | 用法 |
| --- | --- | --- |
| Claude Code CLI | `claude` 可用，npm 全局安装，入口是 `claude.CMD` 包装脚本 | 通过 `--output-format json` 拿结构化结果与成本 |
| Codex CLI | `%LOCALAPPDATA%\OpenAI\Codex\bin\<hash>\codex.exe`，版本 0.167.0-alpha | `codex exec --json` 输出 JSONL 事件流 |
| DSH | `~/.dsh` 下有 70+ skills，会话为 `session.v3.jsonl.zstd`（zstd 压缩 JSONL） | 只读解析，提取会话标题、用户消息、交付物、TODO |
| Zotero | `~/Zotero/zotero.sqlite`（SQLite） | 以 immutable 只读方式打开，避免锁库 |
| Obsidian | 本地 vault | 扫描 TODO 与近期笔记 |
| GitHub | `gh` 已登录（anonymous99-Rise）；HTTPS 直连受限，但 `gh api` 正常 | 用 `gh` 拉通知、指派 issue、待评审 PR |
| 通知 | Windows 11，无第三方通知库 | PowerShell `EncodedCommand` + Windows.UI.Notifications 弹 Toast |
| 运行时 | Python 3.13，Node 24，无 Docker | 核心用标准库，`zstandard` 作为唯一可选第三方依赖 |

## 3. 工具使用经验（踩坑记录）

### Claude Code

- `claude -p --output-format json --dangerously-skip-permissions <prompt>` 可无人值守运行；结果在 `result` 字段，成本在 `modelUsage.*.costUSD`。
- **`--add-dir` 是可变参数**，放在 prompt 前会把 prompt 吞掉，报 "Input must be provided either through stdin or as a prompt argument"。解决办法：不传 `--add-dir`，直接以 cwd 作为工作目录。
- Windows 下 `shutil.which("claude")` 返回 `.CMD` 脚本，直接 `subprocess` 执行会失败或行为异常。需要解析 `.CMD` 内的真实 `.exe` 路径（其中含 `%~dp0` 变量，需替换为脚本所在目录）。
- 单次文献精读任务约 15–37 turns、1–4 分钟、$0.1–$1.3；必须设置超时与每日预算。

### Codex

- `codex exec --skip-git-repo-check --json -C <workspace> -o <last_message> -s danger-full-access <prompt>`。
- 输出是 JSONL，需要逐行解析：`item.completed` 里 `item.type == agent_message` 是正文，`turn.completed` 带 usage。
- 对非 git 目录必须加 `--skip-git-repo-check`，否则直接拒绝执行。
- 沙箱 `-s` 可选 `read-only` / `workspace-write` / `danger-full-access`；无人值守落盘需要后两者。

### DSH 会话

- 会话文件是 zstd 压缩，Python 需 `zstandard`；未安装时采集器应降级为「跳过 + 记录原因」，不能崩溃。
- 文件较大，限制 `max_sessions`，只读尾部与最近的用户消息。
- 交付物路径要从消息内容里提取，不能假设固定目录。

### Zotero

- 运行中的 Zotero 会锁库；用 `file:...?mode=ro&immutable=1` 打开，失败则跳过本轮，不重试轰炸。
- 条目字段在 `itemData` / `itemDataValues`，作者在 `creators`，需要多表 join。

### Windows 通知

- 无 `win10toast` 等依赖时，用 PowerShell 的 `Windows.UI.Notifications.ToastNotificationManager`。
- 命令通过 `-EncodedCommand`（UTF-16LE + Base64）传递，规避中文引号与转义问题。
- 必须设置 AppId（这里用 `Microsoft.Windows.PowerShell`），否则通知可能不显示。

### 进程管理

- Windows 下终止子进程树用 `taskkill /F /T /PID`；`proc.kill()` 只杀直接子进程，Claude/Codex 的子进程会残留。
- 子进程必须 `stdin=DEVNULL`，否则 CLI 可能等待输入挂起。
- `CREATE_NO_WINDOW` 避免后台任务弹黑框。

## 4. 最终架构取舍

| 决策 | 理由 |
| --- | --- |
| 自建 Python 标准库系统，不部署 Multica | 无 Docker；个人单机；需要深度接入本地学术工具 |
| SQLite + WAL 作为唯一状态源 | 单文件、可备份、事务可靠、无需服务端 |
| 线程池调度 + 子进程 Worker | Agent 是外部 CLI，隔离在进程层；线程池足够个人并行度 |
| 启发式规划为主、LLM 规划可选 | 成本可控，且模板 DAG 对常见任务类型已足够 |
| 对外动作强制 Outbox | 与用户约定的信任边界一致：本地自由，外部需确认 |
| 标准库 HTTP + 原生 JS 看板 | 零构建、零依赖、可离线、易迁移 |
| 采集内容标记为不可信 | 防 prompt injection：外部文本只能当素材，不能当指令 |

## 5. 未解决问题

- 微信 / 飞书 / Slack 没有稳定的本地只读接口，暂通过导出文件或 webhook 扩展。
- 语义检索（向量库）未引入；当前用结构化上下文 + 关键词加权，规模小时够用。
- 跨设备同步未做；备份为 JSON 快照，迁移需手动拷贝 `var/` 与 `config/`。
