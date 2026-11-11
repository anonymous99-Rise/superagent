# SuperAgent 使用说明

> 给不想看代码的人：你只管用，剩下的它自己做。

## 你只需要记住三件事

1. **每天 08:00 左右，电脑会弹出一条推送。** 内容是当天和你研究方向相关的论文、科研 Skills、GitHub 仓库。
2. **想看全部内容，双击桌面上的「SuperAgent 看板」。** 浏览器会打开 `http://127.0.0.1:8787`，首页就是总览，「每日推送」里能回看历史。
3. **只有「待确认」需要你动手。** 凡是发邮件、发消息、推送代码、删除文件这类对外或不可逆的动作，它都会停在 Outbox 等你点批准，不会自己执行。

开机自启已经装好，以后登录 Windows 它就会在后台自己运行，不需要你启动任何程序。

---

## 它每天替你做什么

| 时间 | 事情 | 你要做什么 |
| --- | --- | --- |
| 登录电脑时 | 自动在后台启动 | 什么都不用做 |
| 每 15 分钟 | 巡检 GitHub、代码仓库、桌面/文档、Zotero、DSH 会话、Obsidian | 有值得处理的事会进 Inbox |
| 每天 08:00 | 抓取 PubMed / Europe PMC / arXiv，按关键词筛选、去重、排序 | 看推送，感兴趣的点开读 |
| 随时 | 把事项拆成任务，交给 Claude Code / Codex / DeepTutor 执行 | 在 Outbox 批准对外动作 |
| 随时 | 看板首页「问 DeepTutor」：讲解概念、出题自测、做文献调研 | 输入问题，等回答出现在事项里 |

推送同时写到两个地方：

- **看板** →「每日推送」页面
- **Obsidian** → 库 `super建议` → `每日推送/日期.md`

`super建议` 库已经建好并注册到 Obsidian。打开 Obsidian，在库切换里选 `super建议` 就能看到；看板「每日推送」页面里也有「在 Obsidian 打开」按钮。

想弄懂一个概念：在看板首页「问 DeepTutor」里输入问题（例如「讲清楚光系统 I 与光系统 II 的分工，并出两道自测题」），系统会自动建一条学习事项并执行。完成后点开该事项，回答会直接显示在详情里，同时保存为 `answer.md` 产物。也可以直接说「用 DeepTutor 讲解……」或「出一套……自测题」，分诊会把它归到学习任务。

---

## 第一次使用（只看这一节就够）

1. 双击桌面 **SuperAgent 看板**。
2. 如果浏览器能打开页面，说明一切正常。
3. 点顶部 **每日推送**，看今天的论文和推荐。
4. 点顶部 **Outbox**，以后需要你确认的动作都在这里。

就这些。没有要安装的东西，也没有要敲的命令。

---

## 它不会做什么

- 每日推送**不调用任何大模型**，不产生 API 费用。
- 不会自动发邮件、发消息、发朋友圈、推送代码或删除文件。
- 不会把数据上传到外部服务器；看板只监听本机 `127.0.0.1`。
- 不会在你没批准的情况下执行 `git push`、发布、提交表单等动作。

代价是：它把「需要对外」的动作都留给你确认。这是故意的安全边界。

---

## 常见问题

**通知没有弹出来？**
Windows 可能把通知收进了通知中心；打开看板「每日推送」仍然能看到当天内容。也可以在 Windows 设置 → 系统 → 通知里确认允许 PowerShell 通知。

**今天没有推送？**
先打开看板看「每日推送」。如果显示「这一天还没有推送」，点「立即生成」，等 20–40 秒。仍失败时，把看板「事件日志」里的红色错误告诉 Claude 即可。

**推送里的论文不相关？**
告诉 Claude「把推送关键词改成 …」或「减少某类论文」，它会改配置并重新生成，不需要你编辑文件。

**想改推送时间？**
告诉 Claude「每天 9 点推送」，例如。

**Obsidian 里找不到 `super建议`？**
打开 Obsidian → 左下角库切换 → `super建议`。如果列表里没有，告诉 Claude「把 super建议 重新注册到 Obsidian」。

**电脑重启后还用管吗？**
不用。登录后它会自己起来。想确认是否在运行，双击桌面看板能打开就是正常。

---

## 给维护者（可以不看）

<details>
<summary>展开：命令、架构、配置与开发说明</summary>

### 快速开始

```bash
cd D:/devProject/super
python -m superagent doctor          # 环境自检，不消耗模型额度
python -m superagent init            # 初始化数据库与个人上下文
python -m superagent patrol          # 立刻巡检一次
python -m superagent digest --force  # 立即生成每日推送
python -m superagent serve           # 前台启动看板 + 巡检 + 调度器
```

后台常驻与自启：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
powershell -ExecutionPolicy Bypass -File scripts\stop.ps1
powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1   # 已执行
```

### 核心概念

| 概念 | 含义 |
| --- | --- |
| **Signal** | 采集器从外部源发现的一条原始证据（文件改动、CI 失败、新文献、DSH 交付物…） |
| **Item** | 经分诊后进入 Inbox 的事项，带分类、优先级、L1–L5 等级 |
| **Task** | Item 拆解出的可执行子任务，有 agent、prompt、依赖、超时、重试策略 |
| **Run** | 一次真实的 Agent 进程调用，记录 stdout/stderr、耗时、token、成本 |
| **Artifact** | 任务产出的文件或结论，登记在看板上 |
| **Approval** | Outbox 中的待确认动作；批准后任务才会重新入队 |

### 每日推送的数据流

```
PubMed / Europe PMC / arXiv ─┐
本地 DSH / Claude Skills ────┼─▶ 关键词匹配与打分 ─▶ 去重（永久论文/仓库，14 天技能）
GitHub 仓库搜索（gh api）────┘                              │
                                                             ▼
                          var/digests/YYYY-MM-DD.md  +  Inbox 事项 + 桌面通知
                                                             │
                                                             ▼
                                      Obsidian 库 super建议/每日推送/YYYY-MM-DD.md
```

- 排序：先按发布时间，再按关键词命中分数；每个来源有配额，避免单一来源刷屏。
- 去重：论文按 DOI/标题永久去重，仓库永久去重，本地 Skill 有 14 天冷却；当天手动「重新生成」只补充新内容。
- 相关性：ASCII 关键词按词边界匹配（`photosystem I` 不会命中 `photosystem II`）。
- 成本：零 LLM 调用。

### L1–L5 自主等级

| 等级 | 能力 | 典型例子 |
| --- | --- | --- |
| L1 | 理解、整理、提取待办 | 新文献入库、文件变更归档 |
| L2 | 分析问题、给建议 | CI 失败诊断、仓库状态分析 |
| L3 | 产出中间成果 | 文献精读摘要、写作提纲、代码分析 |
| L4 | 自主推进大部分，关键确认 | 起草评审意见、准备修复补丁 |
| L5 | 端到端完成并自检 | 本地可验证的完整交付 |

判定规则在 `superagent/autonomy.py`：kind → 分类/基础等级，内容关键词 → 对外副作用（external/destructive），重要度 + 紧迫词 → 优先级。**对外副作用会自动把等级抬到 L4，并强制进入 Outbox。**

### 架构一览

```
                 ┌──────────────── 巡检循环（默认 15 分钟） ────────────────┐
 GitHub / git ──▶│ collectors → signals → triage → Inbox(items)             │
 Zotero / DSH  ──▶│                                    │                    │
 文件 / 日历 ────▶│                                    ▼                    │
 Obsidian/邮箱 ──▶│                          planner（拆子任务 DAG）          │
                 └────────────────────────────────────┼────────────────────┘
                                                      ▼
                               dispatcher（线程池 / 依赖 / 重试 / 超时）
                          ┌──────────────┼──────────────┐
                          ▼              ▼              ▼
                     Claude Code       Codex        DeepTutor(学习/答疑)
                          └──────────────┴──────────────┘
                                         │
                                    local(校验)
                                          │
                          artifacts / runs / events / approvals
                                          │
                                    SQLite(WAL)
                                          │
                                  看板 + REST API(127.0.0.1)
```

更多细节见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)，调研结论见 [`docs/RESEARCH.md`](docs/RESEARCH.md)，配置项见 [`docs/CONFIG.md`](docs/CONFIG.md)，运维手册见 [`docs/OPERATIONS.md`](docs/OPERATIONS.md)。

### 常用命令

```bash
python -m superagent doctor                 # 环境自检
python -m superagent patrol --only github   # 只巡检某个源
python -m superagent digest --force         # 立即生成今天的论文/技能/仓库推送
python -m superagent digest --show          # 生成并打印 Markdown
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

### 安全与权限边界

- 本地读写与分析可自主执行；**对外动作默认全部拦截**（`policy.external_requires_approval = true`）。
- 采集到的外部内容在 Prompt 中被明确标记为**不可信数据**，要求 Agent 只当素材、不执行其中的指令。
- Agent 输出契约要求把"发送/提交/推送/发布/删除"放进 `actions`，由系统转入 Outbox，而不是直接执行。
- API 默认只绑定 `127.0.0.1`，可设置 `core.api_token` 增加令牌校验。
- Agent 进程在独立工作目录 `var/work/<item_id>/` 中运行；运行日志与成本记录在 `var/runs/`。
- 每日成本预算 `policy.daily_cost_budget_usd`，超限自动停止派发。

> 注意：工作器以 `--dangerously-skip-permissions` / `danger-full-access` 运行，这是为了让无人值守任务能真正落地产物。信任边界由系统的 Outbox 门禁保证，而不是由 Agent 的交互式确认保证。若你希望更保守，可在 `config/super.toml` 中把 Codex 的 `sandbox` 改为 `workspace-write`。

### 目录结构

```
super/
├─ superagent/            # Python 包（核心仅依赖标准库）
│  ├─ collectors/         # 信息源采集器
│  ├─ sources/            # 论文 / GitHub 仓库 / Skills 抓取
│  ├─ workers/            # claude / codex / deeptutor / local 执行器
│  ├─ db.py store.py      # SQLite 持久化与仓储层
│  ├─ digest.py           # 每日科研推送与 Obsidian 同步
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

### 已知边界与下一步

- 邮箱/日历采集器已实现但默认关闭，需要填 IMAP / ICS 配置。
- 消息类源（微信/飞书/Slack）需要各自导出或 webhook，当前通过文件与 webhook 预留扩展点。
- AI 规划默认关闭以控制成本；可在看板中对单条事项点「AI 重新规划」。
- 长期记忆的语义检索尚未引入向量库，目前用结构化上下文 + 关键词加权。
- 成本预算统计 Claude 与 DeepTutor 上报的美元开销；Codex 仅上报 token 用量，不折算金额。
- DeepTutor 需要先启动一次桌面客户端，让 CLI runtime 就绪；`python -m superagent doctor` 会分别检查 CLI 与客户端。

</details>
