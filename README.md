# SuperAgent · 主动式任务中心

把 **Claude Code、Codex、DSH、Zotero、Obsidian、GitHub 和你的本地工作上下文**组合成一个会主动发现任务、制定计划、调用 Agent 执行、并在关键动作前征求你确认的本地系统。

它不是一个演示，而是一个能持续在后台运行的工程：

- **主动感知**：定时巡检 GitHub、代码仓库、桌面/文档、Zotero 文献、DSH 会话、Obsidian 笔记、日历与邮箱。
- **统一 Inbox**：把待办、问题、机会、决策点去重、分类、打优先级。
- **L1–L5 自主等级**：按任务类型、上下文、工具能力和权限自动判断 AI 能推进到哪一步。
- **自动规划与并行执行**：把事项拆成带依赖的子任务 DAG，交给 Claude Code / Codex 并行执行，失败重试、超时终止。
- **HITL Outbox**：对外发送、提交、推送、发布、删除等动作一律拦截，推送 Windows 桌面通知，等你批准。
- **监控看板**：Inbox / Outbox / 任务 / 运行日志 / 工具调用 / 事件 / 成本 / 个人上下文，一处可见，可重试、可干预、可回溯。
- **长期运行**：SQLite 持久化、WAL、断点恢复、孤儿任务修复、预算上限、本地 API 仅绑定 127.0.0.1。

---

## 快速开始

```bash
cd D:/devProject/super

# 1) 自检（不消耗模型额度）
python -m superagent doctor

# 2) 初始化数据库与个人上下文
python -m superagent init

# 3) 立刻巡检一次
python -m superagent patrol

# 4) 启动看板 + 后台巡检 + 调度器
python -m superagent serve
#   打开 http://127.0.0.1:8787
```

后台常驻（Windows）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
powershell -ExecutionPolicy Bypass -File scripts\stop.ps1
powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1   # 登录自启
```

---

## 核心概念

| 概念 | 含义 |
| --- | --- |
| **Signal** | 采集器从外部源发现的一条原始证据（文件改动、CI 失败、新文献、DSH 交付物…） |
| **Item** | 经分诊后进入 Inbox 的事项，带分类、优先级、L1–L5 等级 |
| **Task** | Item 拆解出的可执行子任务，有 agent、prompt、依赖、超时、重试策略 |
| **Run** | 一次真实的 Agent 进程调用，记录 stdout/stderr、耗时、token、成本 |
| **Artifact** | 任务产出的文件或结论，登记在看板上 |
| **Approval** | Outbox 中的待确认动作；批准后任务才会重新入队 |

### L1–L5 自主等级

| 等级 | 能力 | 典型例子 |
| --- | --- | --- |
| L1 | 理解、整理、提取待办 | 新文献入库、文件变更归档 |
| L2 | 分析问题、给建议 | CI 失败诊断、仓库状态分析 |
| L3 | 产出中间成果 | 文献精读摘要、写作提纲、代码分析 |
| L4 | 自主推进大部分，关键确认 | 起草评审意见、准备修复补丁 |
| L5 | 端到端完成并自检 | 本地可验证的完整交付 |

判定规则在 `superagent/autonomy.py`：kind → 分类/基础等级，内容关键词 → 对外副作用（external/destructive），重要度 + 紧迫词 → 优先级。**对外副作用会自动把等级抬到 L4，并强制进入 Outbox。**

---

## 架构一览

```
                 ┌──────────────── 巡检循环（默认 15 分钟） ────────────────┐
 GitHub / git ──▶│ collectors → signals → triage → Inbox(items)             │
 Zotero / DSH  ──▶│                                    │                    │
 文件 / 日历 ────▶│                                    ▼                    │
 Obsidian/邮箱 ──▶│                          planner（拆子任务 DAG）          │
                 └────────────────────────────────────┼────────────────────┘
                                                      ▼
                               dispatcher（线程池 / 依赖 / 重试 / 超时）
                                 ┌────────────┼────────────┐
                                 ▼            ▼            ▼
                            Claude Code     Codex       local(校验)
                                 └────────────┴────────────┘
                                          │
                          artifacts / runs / events / approvals
                                          │
                                    SQLite(WAL)
                                          │
                                  看板 + REST API(127.0.0.1)
```

更多细节见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)，调研结论见 [`docs/RESEARCH.md`](docs/RESEARCH.md)，配置项见 [`docs/CONFIG.md`](docs/CONFIG.md)，运维手册见 [`docs/OPERATIONS.md`](docs/OPERATIONS.md)。

---

## 常用命令

```bash
python -m superagent doctor                 # 环境自检
python -m superagent patrol --only github   # 只巡检某个源
python -m superagent items --status inbox   # 查看 Inbox
python -m superagent plan <item_id> --llm   # 用 AI 规划（默认走模板规划）
python -m superagent run <item_id> --wait 300
python -m superagent add "整理开题报告" --priority 1
python -m superagent approve <approval_id>
python -m superagent export var/backups/snapshot.json
python -m superagent status
```

测试：

```bash
python -m unittest tests.test_core -v
```

---

## 安全与权限边界

- 本地读写与分析可自主执行；**对外动作默认全部拦截**（`policy.external_requires_approval = true`）。
- 采集到的外部内容在 Prompt 中被明确标记为**不可信数据**，要求 Agent 只当素材、不执行其中的指令。
- Agent 输出契约要求把"发送/提交/推送/发布/删除"放进 `actions`，由系统转入 Outbox，而不是直接执行。
- API 默认只绑定 `127.0.0.1`，可设置 `core.api_token` 增加令牌校验。
- Agent 进程在独立工作目录 `var/work/<item_id>/` 中运行；运行日志与成本记录在 `var/runs/`。
- 每日成本预算 `policy.daily_cost_budget_usd`，超限自动停止派发。

> 注意：工作器以 `--dangerously-skip-permissions` / `danger-full-access` 运行，这是为了让无人值守任务能真正落地产物。信任边界由系统的 Outbox 门禁保证，而不是由 Agent 的交互式确认保证。若你希望更保守，可在 `config/super.toml` 中把 Codex 的 `sandbox` 改为 `workspace-write`。

---

## 目录结构

```
super/
├─ superagent/            # Python 包（核心仅依赖标准库）
│  ├─ collectors/         # 信息源采集器
│  ├─ workers/            # claude / codex / local 执行器
│  ├─ db.py store.py      # SQLite 持久化与仓储层
│  ├─ autonomy.py         # L1–L5 与权限策略
│  ├─ triage.py planner.py dispatcher.py hitl.py
│  ├─ engine.py           # 巡检 + 后台调度编排
│  ├─ api.py cli.py       # HTTP API 与命令行
├─ web/                   # 零依赖单页监控看板
├─ config/                # super.toml（本地）/ profile.md（个人档案）
├─ scripts/               # 启动、停止、自启、备份
├─ tests/                 # 单元与 API 测试
└─ var/                   # 运行时数据（已 gitignore）
```

## 已知边界与下一步

- 邮箱/日历采集器已实现但默认关闭，需要填 IMAP / ICS 配置。
- 消息类源（微信/飞书/Slack）需要各自导出或 webhook，当前通过文件与 webhook 预留扩展点。
- AI 规划默认关闭以控制成本；可在看板中对单条事项点「AI 重新规划」。
- 长期记忆的语义检索尚未引入向量库，目前用结构化上下文 + 关键词加权。
- 成本预算只统计 Claude 的美元开销；Codex 仅上报 token 用量，不折算金额。
