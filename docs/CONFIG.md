# 配置说明

配置文件默认读取 `config/super.toml`，可用环境变量 `SUPER_CONFIG` 指定其他路径。
运行时数据默认在 `var/`：`var/super.db` 为唯一状态库，`var/runs/` 存每次运行日志，`var/work/<item_id>/` 存任务产物。

修改配置后需重启进程生效。

## [core]

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `data_dir` | `var` | 运行时数据根目录（相对仓库根，或绝对路径） |
| `db_path` | `var/super.db` | SQLite 文件 |
| `log_level` | `INFO` | 预留日志级别 |
| `api_host` | `127.0.0.1` | 看板监听地址，不要改成 `0.0.0.0` 除非明确需要 |
| `api_port` | `8787` | 看板端口 |
| `timezone` | `Asia/Shanghai` | 展示时区 |
| `workspace_dir` | `D:/devProject` | 默认可读工作区根 |

## [patrol]

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 是否启用后台巡检 |
| `interval_sec` | `900` | 巡检间隔（秒） |
| `run_on_start` | `true` | 启动时立即巡检一次 |
| `jitter_sec` | `30` | 随机抖动，避免固定节奏（实现中预留给外层调度） |

## [policy]

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `external_requires_approval` | `true` | 对外动作是否必须人工批准 |
| `destructive_requires_approval` | `true` | 破坏性动作是否必须人工批准 |
| `max_parallel_tasks` | `3` | 线程池并行上限 |
| `default_timeout_sec` | `900` | 单任务默认超时 |
| `max_attempts` | `2` | 默认最大尝试次数（含首次） |
| `auto_plan` | `true` | 是否自动规划高优先级事项 |
| `auto_plan_per_cycle` | `2` | 每轮巡检最多自动规划数量 |
| `max_pending_backlog` | `12` | 待执行任务达到该数量时暂停自动规划，防止离线堆积 |
| `auto_run_max_level` | `4` | 允许自动执行的最高自主等级 |
| `daily_cost_budget_usd` | `5.0` | 每日 Agent 成本上限，超出即停止派发 |

## [workers.claude]

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 是否启用 |
| `command` | `claude` | 可执行文件；允许 `.CMD`，内部会自动解析真实 `.exe` |
| `model` | `""` | 为空使用 CLI 默认模型 |
| `extra_args` | `[]` | 追加到命令行的参数 |
| `timeout_sec` | `900` | 单次调用超时 |

## [workers.codex]

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 是否启用 |
| `command` | 自动探测 | 留空时在 `%LOCALAPPDATA%\OpenAI\Codex\bin\*\codex.exe` 中找最新 |
| `sandbox` | `danger-full-access` | `read-only` / `workspace-write` / `danger-full-access` |
| `model` | `""` | 为空使用 CLI 默认 |
| `extra_args` | `[]` | 追加参数 |
| `timeout_sec` | `900` | 单次调用超时 |

> 更保守的配置：把 `sandbox` 改为 `workspace-write`。无人值守任务仍能写自己的工作目录，但无法改动系统其他位置。

## [workers.deeptutor]

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 是否启用 |
| `command` | `deeptutor` | CLI 启动器；Windows 下允许 `.cmd` |
| `language` | `zh` | 回答语言，传给 `-l` |
| `capability` | `auto` | `auto` 按问题关键词选择；也可固定为 `chat` / `deep_research` / `deep_question` / `deep_solve` / `visualize` / `mastery_path` |
| `client_url` | `http://127.0.0.1:18801` | 桌面客户端后端地址，仅用于 `doctor` 健康检查；端口随客户端启动可能变化 |
| `extra_args` | `[]` | 追加参数 |
| `timeout_sec` | `600` | 单次调用超时 |

DeepTutor 通过 `run <capability> <message> --format json` 调用，解析 JSONL 事件流；最终回答写入事项工作目录的 `answer.md`，并登记为产物。`python -m superagent doctor` 会分别检查 `deeptutor doctor`（模型配置、凭据、runtime）和客户端 `/health`。

## [notify]

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `desktop` | `true` | 是否发 Windows Toast |
| `min_priority` | `2` | 只对优先级 ≤ 该值的事项通知 |
| `cooldown_sec` | `120` | 同标题通知冷却时间 |

## [collectors.filesystem]

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | |
| `paths` | 桌面、文档 | 扫描根目录列表 |
| `recency_days` | `14` | 只看最近 N 天修改的文件 |
| `max_files` | `500` | 单轮最多检查文件数 |
| `extensions` | `.md .txt .docx .pdf .xlsx .pptx .tex .csv .ipynb` | 关注的文件类型 |

## [collectors.git]

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | |
| `roots` | `["D:/devProject"]` | 仓库搜索根 |
| `recency_days` | `14` | 只看近期活动 |
| `max_repos` | `50` | 最多检查仓库数 |

产出：未提交改动、未推送提交、近期提交活跃度。

## [collectors.github]

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 依赖 `gh` 已登录 |
| `limit` | `30` | 每类查询条数 |
| `include_notifications` | `true` | 是否拉取 GitHub 通知 |

产出：通知、指派给我的 issue、请求我评审的 PR、我开的 PR。

## [collectors.zotero]

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | |
| `db_path` | `~/Zotero/zotero.sqlite` | 只读打开，Zotero 运行中也可读 |
| `recency_days` | `45` | 新入库文献的时间窗口 |

## [collectors.dsh]

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | |
| `home` | `~/.dsh` | DSH 数据目录 |
| `max_sessions` | `40` | 单轮最多解析会话数 |

依赖 `pip install zstandard`；缺失时采集器记录错误并跳过，不影响其他源。

## [collectors.obsidian]

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | |
| `vault` | `~/Documents/Obsidian Vault` | vault 根目录 |

扫描未完成 TODO 与近期修改笔记。

## [collectors.calendar]

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `false` | 默认关闭 |
| `paths` | `["C:/Users/27608/Desktop"]` | 搜索 `.ics` 的目录 |
| `lookahead_days` | `14` | 未来 N 天事件 |

## [collectors.email]

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `false` | 默认关闭 |
| `host` / `port` | `""` / `993` | IMAP 服务器（SSL） |
| `username` | `""` | 邮箱账号 |
| `password_env` | `SUPER_EMAIL_PASSWORD` | 从该环境变量读取密码，**不要写进配置文件** |
| `mailbox` / `limit` / `recency_days` | `INBOX` / `20` / `7` | 读取范围 |

## [digest]

每日论文 / 科研 Skills / GitHub 仓库推送。不调用大模型，无 API 成本。

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 是否启用后台每日推送 |
| `time` | `08:00` | 本地时间；守护进程在到达该时间后生成当天推送 |
| `notify` | `true` | 是否发 Windows Toast |
| `lookback_days` | `7` | 论文时间窗口 |
| `max_papers` / `max_per_source` | `8` / `4` | 总条数与单来源上限 |
| `min_paper_score` | `3.0` | 最低相关分；标题命中 4、摘要命中 1.5 |
| `sources` | `["pubmed","europepmc","arxiv"]` | 论文数据源，可增删 |
| `keywords` | 课题关键词列表 | 建议包含 PSI/PsaE/cyanobacteria/plastid 等 |
| `max_skills` | `4` | 本地技能推荐数（按名称前缀轮转） |
| `skill_cooldown_days` | `14` | 同一技能再次推荐的冷却天数 |
| `skill_dirs` | `~/.dsh/skills` 等 | 扫描 `SKILL.md` 的根目录 |
| `max_github_skills` | `2` | GitHub 技能仓库推荐数 |
| `github_skills_queries` | topic 搜索 | GitHub 技能仓库查询 |
| `github_skills_keywords` | research/academic… | 技能仓库必须命中的语义词 |
| `max_repos` / `min_repo_stars` | `5` / `10` | 仓库数量与最低星标 |
| `repo_max_age_days` | `730` | 只看最近推送过的仓库 |
| `repo_queries` | 7 条主题查询 | 每个查询单独调用 `gh api`，并行执行 |
| `exclude_users` | 自己 | 排除自己的仓库 |
| `blocked_repo_terms` | 盗版/影子图书馆等 | 命中即过滤 |
| `obsidian_sync` | `true` | 是否把每天的推送同步进 Obsidian 库 |
| `obsidian_vault` | `D:/devProject/super建议` | 目标库根目录；不存在会自动创建 `每日推送/` 与 `首页.md` |

> `gh` 需要已登录（`gh auth status`）。技能仓库查询与普通仓库查询共用 GitHub Search 速率限制；默认每天约 10 次请求，远低于限额。

## API 令牌（可选）

在 `[core]` 增加 `api_token = "..."` 后，所有 `/api/*` 请求必须携带令牌，二选一：

- 请求头 `X-Super-Token: <token>`
- 查询参数 `?token=<token>`

看板从地址栏读取 `token` 参数并附加到后续请求，因此设置令牌后用
`http://127.0.0.1:8787/?token=<token>` 打开即可。适合把端口暴露到局域网时使用。

## 配置迁移

`config/super.toml` 含本机绝对路径与个人信息，**不纳入版本管理**。迁移到新机器时：

1. 复制 `config/super.example.toml` 为 `config/super.toml`。
2. 按上表改路径。
3. `python -m superagent doctor` 自检。
4. 如需保留历史，把旧机器的 `var/` 整个拷过来。
