# 运维手册

## 1. 环境要求

| 项 | 要求 |
| --- | --- |
| 系统 | Windows 10/11（PowerShell 5.1+） |
| Python | 3.11+（开发验证于 3.13） |
| 可选 | Claude Code CLI、Codex CLI、`gh`（GitHub）、Zotero、DSH、Obsidian |
| Python 包 | 标准库即可；`zstandard` 用于 DSH 会话解析 |

安装可选依赖：

```bash
python -m pip install zstandard
```

## 2. 首次部署

```bash
cd D:/devProject/super
cp config/super.example.toml config/super.toml   # 按需改路径
python -m superagent doctor
python -m superagent init
python -m superagent patrol
python -m superagent serve
```

`doctor` 会检查：Python 版本、数据库可写、各 Worker 是否可用、各采集器路径是否存在、桌面通知是否可用。
任何一项失败都会给出原因，不会静默跳过。

## 3. 日常运行

### 启动 / 停止（推荐）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start.ps1    # 隐藏窗口启动
powershell -ExecutionPolicy Bypass -File scripts\stop.ps1     # 连同子进程一起结束
```

`start.ps1` 把 PID 写入 `var/daemon.pid`，日志写 `var/logs/daemon.log`（错误输出 `daemon.err.log`）；重复启动会检测已有进程。

### 登录自启（可选）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1
powershell -ExecutionPolicy Bypass -File scripts\uninstall-autostart.ps1
```

注册的是**当前用户**的计划任务 `SuperAgent`，登录时触发，失败重试 3 次；同时在桌面写入「SuperAgent 看板」快捷方式（指向 `http://127.0.0.1:8787`）。卸载脚本只删除计划任务，不删桌面快捷方式。

### 前台运行（调试）

```bash
python -m superagent serve --port 8787
```

## 4. 看板使用

打开 `http://127.0.0.1:8787`。

| 页面 | 用途 |
| --- | --- |
| 总览 | 事项/任务/审批/成本/采集器健康、Worker 可用性 |
| 每日推送 | 当天论文/Skills/仓库推荐，可回看历史日期或手动重新生成 |
| Inbox | 分诊后的事项，可按状态筛选；可规划、执行、归档 |
| Outbox | 待确认的对外/破坏性动作；批准或驳回，可写备注 |
| 任务 | 所有子任务与状态；可重试、取消、查看运行日志 |
| 运行记录 | 每次 Agent 调用的耗时、成本、退出码、stdout/stderr |
| 事件日志 | 系统审计流水，可按级别过滤 |
| 个人上下文 | 编辑用于规划/加权的长期事实 |
| 采集器 | 每个源的最后运行时间、错误、产出统计 |

页面每 4 秒轮询一次；所有动态内容都经过 HTML 转义。

## 5. 数据与备份

| 路径 | 内容 | 是否入库 |
| --- | --- | --- |
| `var/super.db` | 全部状态（SQLite WAL） | 否 |
| `var/runs/<run_id>/` | 每次运行的 stdout/stderr | 否 |
| `var/work/<item_id>/` | 任务产物 | 否 |
| `var/digests/YYYY-MM-DD.md` | 每日科研推送正文 | 否 |
| `D:/devProject/super建议/每日推送/` | 同步给 Obsidian 的推送副本与 `首页.md` 索引 | 否 |
| `var/backups/` | JSON 快照 | 否 |
| `config/super.toml` | 本机配置（含绝对路径） | 否 |
| `config/profile.md` | 个人档案（含个人信息） | 否；仅 `profile.example.md` 入库 |

备份：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\backup.ps1
```

恢复：停进程 → 用备份的 `super.db` 覆盖 `var/super.db`（连同 `-wal`/`-shm` 一起删除）→ 启动。
JSON 快照用于跨版本迁移与人工查阅，不直接回灌数据库。

## 6. 成本控制

- `policy.daily_cost_budget_usd`（默认 $5）按 UTC 日期累计；超限后调度器停止派发并写 `budget.exceeded` 事件。
- 单个任务超时 `workers.*.timeout_sec` / 任务自身 `timeout_sec`，超时强杀进程树。
- 单轮采集有 `SOURCE_CAP` 与 `max_files` 限制，避免一次产生上百事项。
- 实测成本参考（Claude，2026-11）：文献精读 15–37 turns，约 $0.1–$1.3/任务；起草短回复约 $0.1。
- **注意**：Codex 只上报 token 用量，未折算美元，因此预算统计只覆盖 Claude；用 Codex 跑长任务时需自行留意额度。

## 7. 常见问题

### `claude` worker 报 "executable not found" / 直接执行 `.CMD` 失败

系统里 `claude` 是 npm 生成的 `claude.CMD`。`ClaudeWorker.resolve_exe()` 会解析其中的真实 `.exe`。
若仍失败，在 `config/super.toml` 的 `workers.claude.command` 写绝对路径，例如
`C:/Users/<你>/AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/cli.js` 对应的 `node.exe` 启动方式，或直接用 `.exe` 路径。

### Claude 报 "Input must be provided either through stdin or as a prompt argument"

不要把 `--add-dir` 放进 `extra_args`：它是可变参数，会吞掉后面的 prompt。工作目录已由调度器设置为 `var/work/<item_id>/`。

### 任务一直 `running`，但进程已经结束

正常路径下不会发生；若进程被强杀，重启时 `_repair_orphans()` 会把遗留任务标记为 `failed`（错误：`interrupted (process restart)`），并给出通知。
若某次运行的 stdout 完整、只是收尾失败，可以用 `python -m superagent` 的日志定位，然后手动 `retry`。

### Codex 拒绝执行

- 非 git 目录必须带 `--skip-git-repo-check`（已内置）。
- 沙箱过严会拒绝写文件：需要落盘时用 `workspace-write` 或 `danger-full-access`。

### DSH 采集器报 `No module named zstandard`

```bash
python -m pip install zstandard
```

未安装时该采集器会记录错误并跳过，不影响其他源。

### Zotero 采集器报 database is locked

采集器以 `immutable=1` 只读打开，通常不会锁库。若仍失败，关闭 Zotero 后重试，或把 `zotero.sqlite` 复制一份并修改 `collectors.zotero.db_path`。

### 通知不显示

- 检查 Windows 通知设置是否允许 PowerShell 发送通知。
- 检查 `notify.desktop = true`、`notify.min_priority` 与冷却时间。
- 手动验证：`python -m superagent doctor` 会尝试发送一条测试通知。

### 每日推送没有生成 / 内容为空

- 检查 `digest.enabled = true`、`digest.time` 是否已到；后台循环每 60 秒检查一次，生成约需 15–40 秒。
- 手动执行 `python -m superagent digest --force --show` 查看各数据源错误（PubMed / Europe PMC / arXiv / `gh`）。
- 若昨天已推过同样的论文/仓库，会被 `digest_seen` 去重；当天手动“重新生成”会保留当天已选内容，只补充新内容。
- GitHub 相关为空时先运行 `gh auth status`；技能仓库需要元数据命中科研词，过度冷门的主题可能当天没有结果。
- Obsidian 里没有当天笔记：确认 `digest.obsidian_sync = true`、`obsidian_vault` 路径存在；同步失败会记 `digest.obsidian_failed` 事件但不会影响看板推送。库没有出现在 Obsidian 库列表时，重新把该路径注册进 `%APPDATA%\obsidian\obsidian.json`（修改前先备份）。

### 端口被占用

```bash
python -m superagent status
netstat -ano | findstr :8787
```

改 `core.api_port` 或用 `--port` 启动。

## 8. 升级与迁移

- 代码升级：拉取新版本 → 运行测试 `python -m unittest tests.test_core` → 重启。
- 数据库升级：`db.init_db()` 在启动时做前向迁移（如 `tasks.next_attempt_at`），只增列不删列。
- 迁移到新机器：拷贝仓库 + `var/` + `config/super.toml`，改绝对路径，`doctor` 自检。

## 9. 安全操作建议

- 不要把 `api_host` 改成 `0.0.0.0` 而不设 `api_token`。
- 不要把邮箱密码写进 `super.toml`，用 `password_env` 指定的环境变量。
- 定期检查 Outbox；批准前确认收件人、渠道与内容。
- 若希望 Codex 更保守，把 `workers.codex.sandbox` 改为 `workspace-write`。
- 对外发布/推送类动作永远走 Outbox，不要给 Agent 直接凭证。
