# 架构设计

## 1. 分层

```
┌────────────────────────────────────────────────────────────┐
│ 接口层   CLI (superagent/cli.py)   HTTP API (superagent/api.py) │
│          web/ 看板（原生 JS 单页）                              │
├────────────────────────────────────────────────────────────┤
│ 编排层   Engine（巡检循环 + 调度循环 + 上下文刷新）              │
│          Dispatcher（线程池 / DAG / 重试 / 超时）               │
│          Planner（模板或 LLM 规划）                             │
├────────────────────────────────────────────────────────────┤
│ 领域层   Triage（分诊/去重/优先级）  autonomy（L1–L5/权限）      │
│          HITL（Outbox 审批）        Notifier（桌面通知）        │
├────────────────────────────────────────────────────────────┤
│ 执行层   workers/{claude,codex,local}                         │
├────────────────────────────────────────────────────────────┤
│ 采集层   collectors/{filesystem,git,github,zotero,dsh,         │
│            obsidian,calendar,email}                           │
├────────────────────────────────────────────────────────────┤
│ 存储层   SQLite (WAL)  superagent/db.py + store.py             │
└────────────────────────────────────────────────────────────┘
```

核心包只依赖 Python 标准库；`zstandard` 仅 DSH 采集器需要，缺失时该采集器降级跳过。

## 2. 数据模型

| 表 | 关键字段 | 说明 |
| --- | --- | --- |
| `signals` | source, kind, title, body, url, importance, fingerprint, occurred_at | 原始信号；`fingerprint` 唯一，重复采集自动忽略 |
| `items` | title, summary, category, priority, autonomy_level, status, project, why_now, recommendation | Inbox 事项 |
| `tasks` | item_id, agent, prompt, status, depends_on(JSON), attempt, max_attempts, timeout_sec, workspace, next_attempt_at | 可执行子任务 |
| `runs` | task_id, agent, attempt, status, pid, stdout_path, stderr_path, usage_json, duration_ms | 每次真实进程调用 |
| `approvals` | item_id, task_id, kind, title, payload_json, risk, status, decided_by | Outbox 待确认动作 |
| `artifacts` | task_id, item_id, kind, title, path, meta_json | 交付物登记 |
| `events` | ts, level, action, message, actor, entity_type, entity_id | 审计日志 |
| `context` | category, key, value, confidence, source | 个人上下文事实 |
| `collector_state` | name, last_run_at, last_ok_at, cursor_json, error, stats_json | 采集器游标与健康状态 |

状态机：

- item：`inbox → planned → running → (done | failed | needs_approval | canceled)`
- task：`pending → running → (succeeded | failed | canceled | needs_approval)`
- approval：`pending → (approved | rejected)`，决策幂等
- run：`running → (succeeded | failed | interrupted)`

## 3. 主流程

### 3.1 巡检（默认 15 分钟）

```
Engine.patrol_once()
  ├─ 对每个启用的 collector 调 collect()
  ├─ Store.add_signal()（fingerprint 去重）
  ├─ Triage.run()：
  │     · SOURCE_CAP 限制单源单轮产量
  │     · autonomy.assess() 得到 category / level / priority / side_effect
  │     · 低重要度信号聚合为 digest 事项，避免刷屏
  │     · 用个人上下文关键词加权
  │     → create_item()（open_item_with_title 做标题级去重）
  ├─ Context.refresh()：从近期事项推导活跃项目/主题
  └─ 若 auto_plan：挑 priority ≤ 2 且 level ≥ 2 的事项自动规划
```

巡检有 `_patrol_lock`，后台循环与手动触发不会并发。

### 3.2 规划

`Planner.plan_item(item_id, use_llm=False)`：

- 模板规划：按 category 选择 DAG（reading 2 步、writing 3 步、coding 3 步…），每一步指定 agent 与 prompt。
- LLM 规划：调用 Claude 输出 JSON 计划，失败自动回退模板。
- 物化：为每个任务建 `var/work/<item_id>/` 工作目录，写入 `depends_on`，item 置为 `planned`。
- 幂等：已有任务的事项再次规划直接返回原任务 ID。

### 3.3 调度

`Dispatcher.tick()` 由后台循环每 5 秒调用：

1. `_reap()`：回收已完成的 future，记录异常。
2. 预算检查：`today_cost() ≥ daily_cost_budget_usd` 则停止派发。
3. 容量：`max_parallel_tasks - 运行中`。
4. `_ready_tasks()`：pending 且 `next_attempt_at` 已到、依赖全部 `succeeded/skipped`。
5. 审批门禁：`requires_approval` 且未获批 → 创建 approval，任务转 `needs_approval`。
6. `claim_task()` 原子抢占（`UPDATE ... WHERE status='pending'`），创建 run，提交线程池。

依赖失败时下游任务转 `canceled`；若上游后来恢复成功，`_finalize_success` 会把因依赖被取消的下游**复活**为 pending。

### 3.4 执行与收尾

Worker 统一接口：

```python
class Worker:
    def available(self) -> tuple[bool, str]
    def run(prompt, workspace, timeout, stdout_path, stderr_path,
            on_pid, extra) -> WorkerResult
```

- 子进程 `stdin=DEVNULL`，stdout/stderr 落盘到 `var/runs/<run_id>/`。
- 超时用 `proc.wait(timeout)`，超时后 `kill_tree(pid)`。
- 成功：登记结构化 artifacts + 扫描工作目录新文件；`actions` 转为 Outbox 审批；任务 `succeeded`。
- 失败：`attempt < max_attempts` 时按 `min(600, 60·2^(n-1))` 退避重试，否则 `failed` 并推送通知。
- 收尾异常不会把任务永久留在 `running`：捕获后标记 failed 并记录事件。

### 3.5 人工确认（HITL）

- 触发条件：任务 `requires_approval`，或 Agent 输出 `actions` 中的对外/破坏性动作。
- `HITL.gate_task()` 创建 approval、发桌面通知、任务转 `needs_approval`。
- 看板 Outbox 展示背景、建议、可执行选项、风险级别。
- 批准：任务回到 `pending` 继续执行；驳回：任务 `canceled`。
- Agent 被明确要求：**不得直接发送/提交/推送/发布**，只能把动作写进 `actions`。

## 4. 自主等级与权限

`autonomy.assess(kind, title, body, importance)`：

- kind → category（reading/writing/coding/research/communication/calendar/admin/ops/review…）
- 内容关键词 → side_effect（`local` / `external` / `destructive`）
- external/destructive 强制 level ≥ 4 且需要审批
- importance + 紧迫词 → priority 1–5

`autorun_allowed(level, side_effect, cfg)`：等级不超过 `auto_run_max_level`（默认 4）且无对外副作用才能自动执行。

## 5. 上下文

`Context` 存 13 条种子事实（角色、论文方向、技术栈、工具、偏好、信任边界），运行中从事项与项目名刷新活跃主题。

- 注入方式：`Context.text()` 拼进 Planner 的 prompt。
- 关键词加权：`Context.keywords()` 用于 Triage 提升相关信号优先级。
- 全部可在看板「个人上下文」页编辑，存于 `context` 表。

## 6. 安全设计

| 威胁 | 对策 |
| --- | --- |
| Prompt injection（外部内容含指令） | 采集内容标记为「不可信数据」；输出契约要求只提取事实、把动作放进 `actions` |
| Agent 越权对外操作 | Outbox 门禁；Worker 无凭证注入（邮箱密码只通过环境变量给采集器） |
| API 被本机其他程序滥用 | 默认只绑 `127.0.0.1`；可选 `core.api_token` Bearer 校验 |
| 静态目录穿越 | 静态文件服务做路径规范化 + 根目录校验 |
| XSS（看板渲染外部标题） | 所有动态文本经 `escapeHtml()` |
| 成本失控 | 每日预算、单任务超时、单轮产量上限、并行度上限 |
| 数据库损坏 | WAL + busy_timeout + 事务；备份脚本导出 JSON 快照 |

## 7. 失败恢复

- **进程重启**：`Dispatcher._repair_orphans()` 把遗留 `running` 任务标记 failed、遗留 run 标记 interrupted。
- **任务卡死**：超时强杀进程树，按重试策略处理。
- **依赖失败**：下游取消；上游恢复后自动复活。
- **采集器异常**：记录到 `collector_state.error`，不影响其他采集器。
- **预算耗尽**：停止派发并记事件，次日自动恢复（成本按 UTC 日期统计）。
- **单条事项作废**：看板「归档」，不删除历史。

## 8. 扩展点

- 新采集器：实现 `collect() -> list[Signal]` 并在 `collectors/__init__.py` 注册。
- 新执行器：实现 `Worker` 子类，在 `workers/__init__.py` 的 `get_worker()` 注册。
- 新任务类型：在 `autonomy.py` 补 kind 映射，在 `planner.py` 补模板 DAG。
- 消息源（微信/飞书/Slack）：建议先落成本地导出文件，用 filesystem 采集器或新增 webhook 采集器接入。
