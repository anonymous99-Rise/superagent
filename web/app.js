"use strict";

const CATEGORY = {
  research: "研究", reading: "阅读", writing: "写作", coding: "编码", review: "评审",
  ops: "运维", communication: "沟通", calendar: "日程", admin: "事务", document: "文档",
  digest: "推送",
};
const STATUS = {
  inbox: "待分诊", planned: "已规划", running: "执行中", needs_approval: "待确认",
  done: "已完成", failed: "失败", canceled: "已取消", archived: "已归档",
  pending: "排队", retrying: "重试中", succeeded: "成功", skipped: "跳过",
  approved: "已批准", rejected: "已驳回",
};
const LEVEL = { 1: "L1", 2: "L2", 3: "L3", 4: "L4", 5: "L5" };

const state = { view: "overview", itemFilter: "inbox", eventLevel: "", digestDate: "", timer: null, busy: false };

const $ = (s) => document.querySelector(s);
const esc = (v) => String(v == null ? "" : v)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
const fmtTime = (ts) => {
  if (!ts) return "";
  const d = new Date(ts);
  if (isNaN(d.getTime())) return ts;
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getMonth() + 1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
};
const short = (s, n) => (s && s.length > n ? s.slice(0, n - 1) + "…" : (s || ""));

const TOKEN = new URLSearchParams(location.search).get("token") || "";

async function api(path, opts = {}) {
  const init = { headers: { "Content-Type": "application/json" }, ...opts };
  init.headers = { ...init.headers };
  if (TOKEN) init.headers["X-Super-Token"] = TOKEN;
  if (init.body && typeof init.body !== "string") init.body = JSON.stringify(init.body);
  const r = await fetch(path, init);
  const j = await r.json().catch(() => ({ ok: false, error: "bad json" }));
  if (!r.ok || j.ok === false) throw new Error(j.error || `HTTP ${r.status}`);
  return j.data;
}

function toast(msg, isErr = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "toast" + (isErr ? " err" : "");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.add("hidden"), 3200);
}

function setSub(text) { $("#view-sub").textContent = text; }

const TITLES = {
  overview: ["总览", "系统状态、Agent 与采集器概览"],
  digest: ["每日推送", "每日论文、科研 Skills 与 GitHub 仓库推荐"],
  inbox: ["Inbox", "所有被发现的待办、问题、机会与决策点"],
  outbox: ["Outbox", "需要你确认后才会执行的对外动作"],
  tasks: ["任务", "子任务执行状态与产物"],
  runs: ["运行记录", "每次 Agent 调用的日志与成本"],
  events: ["事件日志", "系统审计与执行轨迹"],
  context: ["个人上下文", "Agent 判断优先级所依据的个人信息"],
  collectors: ["采集器", "信息源巡检状态"],
};

function switchView(view) {
  state.view = view;
  document.querySelectorAll(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + view));
  const [t, s] = TITLES[view] || [view, ""];
  $("#view-title").textContent = t;
  setSub(s);
  render();
}

// ---------------- rendering ----------------
async function render() {
  if (state.busy) return;
  state.busy = true;
  try {
    if (state.view === "overview") await renderOverview();
    else if (state.view === "digest") await renderDigest();
    else if (state.view === "inbox") await renderInbox();
    else if (state.view === "outbox") await renderOutbox();
    else if (state.view === "tasks") await renderTasks();
    else if (state.view === "runs") await renderRuns();
    else if (state.view === "events") await renderEvents();
    else if (state.view === "context") await renderContext();
    else if (state.view === "collectors") await renderCollectors();
    await refreshBadges();
    setHealth(true);
  } catch (e) {
    setHealth(false);
    toast(e.message, true);
  } finally {
    state.busy = false;
  }
}

async function refreshBadges() {
  try {
    const d = await api("/api/overview");
    const st = d.stats;
    $("#badge-inbox").textContent = (st.items.inbox || 0) + (st.items.planned || 0);
    $("#badge-outbox").textContent = st.pending_approvals || 0;
    $("#badge-tasks").textContent = (st.tasks.running || 0) + (st.tasks.pending || 0);
    $("#badge-outbox").style.display = st.pending_approvals ? "" : "none";
  } catch (e) { /* ignore */ }
}

function setHealth(ok) {
  $("#health-dot").className = "dot " + (ok ? "ok" : "bad");
  $("#health-text").textContent = ok ? "服务正常" : "连接异常";
}

async function renderOverview() {
  const d = await api("/api/overview");
  const st = d.stats;
  const rate = st.success_rate == null ? "—" : Math.round(st.success_rate * 100) + "%";
  const cards = [
    ["待处理事项", (st.items.inbox || 0) + (st.items.planned || 0), `共 ${Object.values(st.items).reduce((a, b) => a + b, 0)} 条`],
    ["待确认", st.pending_approvals, "Outbox 中等待你决策", st.pending_approvals ? "p2" : ""],
    ["执行中任务", st.tasks.running || 0, `排队 ${st.tasks.pending || 0}`],
    ["今日信号", st.signals_today, `累计 ${st.signals_total}`],
    ["成功率", rate, `${st.runs_total} 次运行`],
    ["今日成本", "$" + (d.cost_today || 0), d.budget ? `预算 $${d.budget}` : "未设预算"],
  ];
  const agents = Object.entries(d.workers).map(([name, w]) => `
    <div class="agent-card">
      <div><span class="dot-mini ${w.available ? "ok" : "bad"}"></span><span class="agent-name">${esc(name)}</span>
        <div class="muted small">${esc(w.available ? "可用" : (w.reason || "不可用"))}</div></div>
      <span class="pill">${name === "claude" ? "分析/写作" : name === "codex" ? "代码/执行" : name === "deeptutor" ? "学习/答疑" : "确定性"}</span>
    </div>`).join("");
  const collectors = (d.collectors || []).map((c) => `
    <tr><td>${esc(c.name)}</td>
      <td><span class="dot-mini ${c.last_ok_at ? "ok" : "bad"}"></span>${c.error ? esc(short(c.error, 40)) : "正常"}</td>
      <td class="nowrap muted">${c.last_run_at ? fmtTime(c.last_run_at) : "—"}</td>
      <td class="muted">${c.stats_json ? esc(short(c.stats_json, 60)) : ""}</td></tr>`).join("");
  const patrol = d.patrol;
  $("#view-overview").innerHTML = `
    <div class="grid stats">${cards.map(([l, v, s, cls]) => `
      <div class="card stat"><div class="label">${esc(l)}</div>
        <div class="value ${cls || ""}">${esc(v)}</div><div class="sub">${esc(s)}</div></div>`).join("")}
    </div>
    <div class="card section-gap">
      <div class="section-title" style="margin-top:0">问 DeepTutor</div>
      <div class="form-row">
        <input id="ask-q" placeholder="例如：讲清楚光系统 I 与光系统 II 的分工，并出两道自测题" style="flex:1;min-width:320px" />
        <button class="btn primary" data-action="ask">提问</button>
      </div>
      <div class="muted small">DeepTutor 会用中文讲解；完整回答保存为事项产物，可在事项详情里直接查看。</div>
    </div>
    <div class="two-col section-gap">
      <div class="card">
        <div class="section-title" style="margin-top:0">最近事件</div>
        <div id="ov-events" class="timeline"><div class="muted small">加载中…</div></div>
        <div class="section-gap"><button class="btn small" data-action="goto" data-view="events">查看全部 →</button></div>
      </div>
      <div class="card">
        <div class="section-title" style="margin-top:0">执行器</div>
        <div class="grid" style="gap:12px">${agents}</div>
        <div class="section-title">巡检</div>
        <div class="kv">
          <div class="k">状态</div><div>${patrol.enabled ? "已启用" : "已关闭"}</div>
          <div class="k">周期</div><div>${Math.round(patrol.interval_sec / 60)} 分钟</div>
          <div class="k">上次巡检</div><div>${patrol.last ? fmtTime(patrol.last) : "尚未运行"}</div>
          <div class="k">上次结果</div><div>${patrol.stats && patrol.stats.signals != null ? `信号 ${patrol.stats.signals} / 新建 ${patrol.stats.created}` : "—"}</div>
        </div>
      </div>
    </div>
    <div class="section-title">采集器</div>
    <div class="card"><table><thead><tr><th>来源</th><th>状态</th><th>最后巡检</th><th>指标</th></tr></thead>
      <tbody>${collectors || '<tr><td colspan="4" class="muted">尚未巡检</td></tr>'}</tbody></table></div>`;
  try {
    const ev = await api("/api/events?limit=8");
    $("#ov-events").innerHTML = ev.length ? ev.map((e) => `
      <div class="tl-row"><div class="ts">${fmtTime(e.ts)}</div>
        <div class="lvl ${esc(e.level)}">${esc(e.level)}</div>
        <div>${esc(short(e.message || e.action, 90))}</div></div>`).join("") : '<div class="muted small">暂无事件</div>';
  } catch (e) { /* ignore */ }
}

// ---------------- digest markdown ----------------
function inlineMd(s) {
  return s
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, text, href) => {
      const raw = href.replace(/&amp;/g, "&");
      if (!/^https?:\/\//i.test(raw)) return text;
      return `<a href="${href}" target="_blank" rel="noopener">${text}</a>`;
    })
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

function mdToHtml(md) {
  const out = [];
  let para = [], list = false, quote = [];
  const flushPara = () => {
    if (para.length) { out.push(`<p>${inlineMd(para.join(" "))}</p>`); para = []; }
  };
  const flushList = () => { if (list) { out.push("</ul>"); list = false; } };
  const flushQuote = () => {
    if (quote.length) { out.push(`<blockquote>${inlineMd(quote.join(" "))}</blockquote>`); quote = []; }
  };
  const flushAll = () => { flushPara(); flushList(); flushQuote(); };
  for (const raw of String(md || "").split("\n")) {
    const line = esc(raw.replace(/\s+$/, ""));
    if (!line.trim()) { flushAll(); continue; }
    const head = line.match(/^(#{1,4})\s+(.*)$/);
    if (head) { flushAll(); out.push(`<h${head[1].length}>${inlineMd(head[2])}</h${head[1].length}>`); continue; }
    if (/^---+$/.test(line.trim())) { flushAll(); out.push("<hr>"); continue; }
    if (/^&gt;\s?/.test(line)) { flushPara(); flushList(); quote.push(line.replace(/^&gt;\s?/, "")); continue; }
    const li = line.match(/^[-*]\s+(.*)$/);
    if (li) {
      flushPara(); flushQuote();
      if (!list) { out.push("<ul>"); list = true; }
      out.push(`<li>${inlineMd(li[1])}</li>`);
      continue;
    }
    flushList(); flushQuote(); para.push(line);
  }
  flushAll();
  return out.join("\n");
}

async function renderDigest() {
  const list = await api("/api/digests?limit=21");
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  const today = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  const dates = list.map((d) => d.date);
  if (!dates.includes(today)) dates.unshift(today);
  if (!state.digestDate || !dates.includes(state.digestDate)) state.digestDate = dates[0];
  const d = await api("/api/digest?date=" + encodeURIComponent(state.digestDate));
  const chips = dates.map((x) => `
    <button class="chip ${x === state.digestDate ? "active" : ""}" data-action="digest-date" data-date="${esc(x)}">
      ${x === today ? "今日 · " : ""}${esc(x.slice(5))}</button>`).join("");
  let body;
  if (d.exists) {
    const c = (d.stats && d.stats.counts) || {};
    const errs = Object.keys((d.stats && d.stats.errors) || {}).length;
    body = `
      <div class="card digest-meta">
        <span class="pill">论文 ${esc(c.papers || 0)}</span>
        <span class="pill">Skills ${esc(c.skills || 0)}</span>
        <span class="pill">仓库 ${esc(c.repos || 0)}</span>
        ${errs ? `<span class="pill p2">数据源异常 ${errs}</span>` : ""}
        <span class="muted small">生成于 ${fmtTime(d.digest.created_at)}</span>
        ${d.item ? `<button class="btn small" data-action="detail" data-id="${esc(d.item.id)}">在 Inbox 查看</button>` : ""}
        <a class="btn small" href="obsidian://open?vault=${encodeURIComponent("super建议")}">在 Obsidian 打开</a>
        <button class="btn small" data-action="digest-run">重新生成</button>
      </div>
      <div class="card md">${mdToHtml(d.markdown || "")}</div>`;
  } else {
    body = `<div class="empty">这一天还没有推送。每天 ${esc("08:00")} 自动生成，也可以立即抓取：
      <div style="margin-top:12px"><button class="btn primary" data-action="digest-run">立即生成</button></div></div>`;
  }
  $("#view-digest").innerHTML = `<div class="chips">${chips}</div>${body}`;
}

function itemCard(it) {
  return `<div class="item-card" data-item="${esc(it.id)}">
    <div class="item-head">
      <div class="item-title">${esc(it.title)}</div>
      <span class="pill p${esc(it.priority)}">P${esc(it.priority)}</span>
      <span class="pill L${esc(it.autonomy_level)}">${LEVEL[it.autonomy_level] || "L?"}</span>
    </div>
    <div class="item-meta">
      <span class="pill">${esc(CATEGORY[it.category] || it.category)}</span>
      <span class="pill status-${esc(it.status)}">${esc(STATUS[it.status] || it.status)}</span>
      ${it.project ? `<span class="pill">${esc(short(it.project, 24))}</span>` : ""}
      <span class="muted small">${fmtTime(it.created_at)}</span>
    </div>
    <div class="item-summary">${esc(short(it.summary || "", 240))}</div>
    <div class="item-actions">
      ${it.status === "inbox" ? `<button class="btn small" data-action="plan" data-id="${esc(it.id)}">规划</button>` : ""}
      ${["inbox", "planned", "failed", "canceled"].includes(it.status) ? `<button class="btn small primary" data-action="run" data-id="${esc(it.id)}">执行</button>` : ""}
      <button class="btn small" data-action="detail" data-id="${esc(it.id)}">详情</button>
      ${!["archived", "done"].includes(it.status) ? `<button class="btn small" data-action="archive" data-id="${esc(it.id)}">归档</button>` : ""}
    </div>
  </div>`;
}

async function renderInbox() {
  const items = await api(`/api/items?status=${encodeURIComponent(state.itemFilter)}`);
  const chips = ["inbox", "planned", "running", "needs_approval", "done", "failed", "archived"];
  const counts = {};
  const all = await api("/api/items?limit=1000");
  all.forEach((i) => { counts[i.status] = (counts[i.status] || 0) + 1; });
  $("#view-inbox").innerHTML = `
    <div class="chips">${chips.map((c) => `
      <button class="chip ${c === state.itemFilter ? "active" : ""}" data-action="filter" data-status="${c}">
        ${esc(STATUS[c] || c)}${counts[c] ? ` · ${counts[c]}` : ""}</button>`).join("")}</div>
    <div class="list">${items.length ? items.map(itemCard).join("") : '<div class="empty">这一栏暂时没有事项</div>'}</div>`;
}

async function renderOutbox() {
  const list = await api("/api/approvals?status=pending");
  $("#view-outbox").innerHTML = list.length ? `<div class="list">${list.map((a) => `
    <div class="item-card">
      <div class="item-head"><div class="item-title">${esc(a.title)}</div>
        <span class="pill p2">${esc(a.risk)}</span></div>
      <div class="item-summary">${esc(a.summary || "")}</div>
      <div class="json-view">${esc(short(JSON.stringify(JSON.parse(a.payload_json || "{}"), null, 2), 1200))}</div>
      <div class="item-actions">
        <button class="btn small ok" data-action="approve" data-id="${esc(a.id)}">批准</button>
        <button class="btn small danger" data-action="reject" data-id="${esc(a.id)}">驳回</button>
        <span class="muted small">${fmtTime(a.created_at)}</span>
      </div>
    </div>`).join("")}</div>` : '<div class="empty">没有需要确认的事项</div>';
}

async function renderTasks() {
  const tasks = await api("/api/tasks?limit=300");
  $("#view-tasks").innerHTML = tasks.length ? `<div class="card"><table>
    <thead><tr><th>状态</th><th>任务</th><th>执行器</th><th>L</th><th>尝试</th><th>更新</th><th></th></tr></thead>
    <tbody>${tasks.map((t) => `<tr>
      <td><span class="pill status-${esc(t.status)}">${esc(STATUS[t.status] || t.status)}</span></td>
      <td><div class="truncate">${esc(t.title)}</div>${t.error ? `<div class="muted small">${esc(short(t.error, 90))}</div>` : ""}</td>
      <td>${esc(t.agent)}</td><td>L${esc(t.autonomy_level)}</td>
      <td>${esc(t.attempt)}/${esc(t.max_attempts)}</td>
      <td class="nowrap muted small">${fmtTime(t.finished_at || t.started_at || t.created_at)}</td>
      <td class="nowrap right">
        <button class="btn small" data-action="task-detail" data-id="${esc(t.id)}">日志</button>
        ${["failed", "canceled", "succeeded"].includes(t.status) ? `<button class="btn small" data-action="retry" data-id="${esc(t.id)}">重试</button>` : ""}
        ${["pending", "running", "needs_approval"].includes(t.status) ? `<button class="btn small danger" data-action="cancel" data-id="${esc(t.id)}">取消</button>` : ""}
      </td></tr>`).join("")}</tbody></table></div>` : '<div class="empty">暂无任务</div>';
}

async function renderRuns() {
  const runs = await api("/api/runs?limit=100");
  $("#view-runs").innerHTML = runs.length ? `<div class="card"><table>
    <thead><tr><th>状态</th><th>Agent</th><th>任务</th><th>耗时</th><th>成本</th><th>时间</th><th></th></tr></thead>
    <tbody>${runs.map((r) => {
      let usage = {}; try { usage = JSON.parse(r.usage_json || "{}"); } catch (e) {}
      return `<tr>
        <td><span class="pill status-${esc(r.status)}">${esc(STATUS[r.status] || r.status)}</span></td>
        <td>${esc(r.agent)}</td>
        <td class="mono muted">${esc(r.task_id)}</td>
        <td class="nowrap">${r.duration_ms != null ? Math.round(r.duration_ms / 1000) + "s" : "—"}</td>
        <td>${usage.cost_usd != null ? "$" + usage.cost_usd : "—"}</td>
        <td class="nowrap muted small">${fmtTime(r.started_at)}</td>
        <td class="right"><button class="btn small" data-action="run-logs" data-id="${esc(r.id)}">日志</button></td>
      </tr>`; }).join("")}</tbody></table></div>` : '<div class="empty">暂无运行记录</div>';
}

async function renderEvents() {
  const q = state.eventLevel ? `&level=${state.eventLevel}` : "";
  const ev = await api(`/api/events?limit=200${q}`);
  const levels = ["", "info", "warn", "error", "debug"];
  $("#view-events").innerHTML = `
    <div class="chips">${levels.map((l) => `<button class="chip ${l === state.eventLevel ? "active" : ""}"
      data-action="event-filter" data-level="${l}">${l || "全部"}</button>`).join("")}</div>
    <div class="card timeline">${ev.length ? ev.map((e) => `
      <div class="tl-row"><div class="ts">${fmtTime(e.ts)}</div>
        <div class="lvl ${esc(e.level)}">${esc(e.level)}</div>
        <div><b>${esc(e.action)}</b> · ${esc(short(e.message || "", 140))}
          ${e.entity_id ? `<span class="muted small mono"> ${esc(e.entity_id)}</span>` : ""}</div></div>`).join("") : '<div class="empty">暂无事件</div>'}
    </div>`;
}

async function renderContext() {
  const rows = await api("/api/context");
  const groups = {};
  rows.forEach((r) => { (groups[r.category] = groups[r.category] || []).push(r); });
  const catName = { profile: "基本身份", project: "项目", topic: "研究主题", preference: "偏好", goal: "目标", tool: "工具", note: "其他" };
  $("#view-context").innerHTML = `
    <div class="card">
      <div class="section-title" style="margin-top:0">新增一条上下文</div>
      <div class="form-row">
        <select id="ctx-cat">${Object.entries(catName).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select>
        <input id="ctx-key" placeholder="键，例如 thesis_deadline" style="min-width:220px" />
        <input id="ctx-val" placeholder="值，例如 2026-12-15 提交初稿" style="min-width:320px;flex:1" />
        <button class="btn primary" data-action="ctx-add">添加</button>
      </div>
    </div>
    ${Object.entries(groups).map(([cat, list]) => `
      <div class="section-title">${esc(catName[cat] || cat)}</div>
      <div class="card"><table><tbody>${list.map((r) => `<tr>
        <td class="mono muted" style="width:170px">${esc(r.key)}</td>
        <td>${esc(r.value)}</td>
        <td class="muted small nowrap">${esc(r.source)} · ${Math.round((r.confidence || 0) * 100)}%</td>
        <td class="right"><button class="btn small danger" data-action="ctx-del" data-key="${esc(r.key)}">删除</button></td>
      </tr>`).join("")}</tbody></table></div>`).join("")}`;
}

async function renderCollectors() {
  const cs = await api("/api/collectors");
  $("#view-collectors").innerHTML = cs.length ? `<div class="card"><table>
    <thead><tr><th>采集器</th><th>状态</th><th>最后巡检</th><th>最近成功</th><th>指标</th><th></th></tr></thead>
    <tbody>${cs.map((c) => `<tr>
      <td><b>${esc(c.name)}</b></td>
      <td><span class="dot-mini ${c.last_ok_at ? "ok" : "bad"}"></span>${c.error ? esc(short(c.error, 60)) : "正常"}</td>
      <td class="nowrap muted small">${fmtTime(c.last_run_at)}</td>
      <td class="nowrap muted small">${fmtTime(c.last_ok_at)}</td>
      <td class="muted small">${esc(short(c.stats_json || "", 80))}</td>
      <td class="right"><button class="btn small" data-action="patrol-one" data-source="${esc(c.name)}">单独巡检</button></td>
    </tr>`).join("")}</tbody></table></div>` : '<div class="empty">尚未运行采集器</div>';
}

// ---------------- detail modals ----------------
async function openItem(id) {
  const d = await api("/api/items/" + id);
  const it = d.item;
  let meta = {}; try { meta = JSON.parse(it.meta_json || "{}"); } catch (e) {}
  const tasks = d.tasks.map((t) => `<tr>
    <td><span class="pill status-${esc(t.status)}">${esc(STATUS[t.status] || t.status)}</span></td>
    <td>${esc(t.title)}</td><td>${esc(t.agent)}</td><td>L${esc(t.autonomy_level)}</td>
    <td>${esc(t.attempt)}/${esc(t.max_attempts)}</td>
    <td class="right"><button class="btn small" data-action="task-detail" data-id="${esc(t.id)}">日志</button></td></tr>`).join("");
  const arts = d.artifacts.map((a) => `<tr><td>${esc(a.kind)}</td>
    <td>${a.path ? `<span class="mono small">${esc(a.path)}</span>` : esc(a.title)}</td>
    <td class="muted small nowrap">${fmtTime(a.created_at)}</td></tr>`).join("");
  const apps = d.approvals.map((a) => `<tr><td>${esc(a.title)}</td>
    <td><span class="pill">${esc(STATUS[a.status] || a.status)}</span></td></tr>`).join("");
  const evs = d.events.map((e) => `<div class="tl-row"><div class="ts">${fmtTime(e.ts)}</div>
    <div class="lvl ${esc(e.level)}">${esc(e.level)}</div><div>${esc(e.action)} · ${esc(short(e.message || "", 100))}</div></div>`).join("");
  $("#modal-title").textContent = it.title;
  $("#modal-body").innerHTML = `
    <div class="kv">
      <div class="k">优先级 / 等级</div><div>P${esc(it.priority)} · ${LEVEL[it.autonomy_level] || ""}（${esc(it.category)}）</div>
      <div class="k">状态</div><div>${esc(STATUS[it.status] || it.status)}</div>
      <div class="k">为什么是现在</div><div>${esc(it.why_now || "—")}</div>
      <div class="k">建议</div><div>${esc(it.recommendation || "—")}</div>
      <div class="k">来源</div><div>${esc(meta.source || "")} / ${esc(meta.kind || "")} ${meta.url ? `· <span class="mono small">${esc(meta.url)}</span>` : ""}</div>
      <div class="k">摘要</div><div style="white-space:pre-wrap">${esc(it.summary || "")}</div>
    </div>
    ${d.answer ? `<div class="section-title">DeepTutor 回答</div><div class="answer-view">${esc(d.answer)}</div>` : ""}
    <div class="form-row">
      <button class="btn small primary" data-action="run" data-id="${esc(it.id)}">执行</button>
      <button class="btn small" data-action="plan" data-id="${esc(it.id)}" data-llm="1">AI 重新规划</button>
      <button class="btn small" data-action="archive" data-id="${esc(it.id)}">归档</button>
    </div>
    <div class="section-title">子任务</div>
    ${tasks ? `<table><thead><tr><th>状态</th><th>任务</th><th>执行器</th><th>等级</th><th>尝试</th><th></th></tr></thead><tbody>${tasks}</tbody></table>` : '<div class="muted small">尚未规划</div>'}
    <div class="section-title">产物</div>
    ${arts ? `<table><tbody>${arts}</tbody></table>` : '<div class="muted small">暂无产物</div>'}
    <div class="section-title">确认记录</div>
    ${apps ? `<table><tbody>${apps}</tbody></table>` : '<div class="muted small">无</div>'}
    <div class="section-title">事件</div>
    <div class="timeline">${evs || '<div class="muted small">无</div>'}</div>`;
  $("#modal").classList.remove("hidden");
}

async function openTask(id) {
  const d = await api("/api/tasks/" + id);
  const t = d.task;
  const runs = d.runs.map((r) => `<tr><td>${esc(r.status)}</td><td>${esc(r.agent)}</td>
    <td>${r.duration_ms != null ? Math.round(r.duration_ms / 1000) + "s" : ""}</td>
    <td class="right"><button class="btn small" data-action="run-logs" data-id="${esc(r.id)}">日志</button></td></tr>`).join("");
  $("#modal-title").textContent = "任务：" + t.title;
  $("#modal-body").innerHTML = `
    <div class="kv">
      <div class="k">状态</div><div>${esc(STATUS[t.status] || t.status)}</div>
      <div class="k">执行器</div><div>${esc(t.agent)} · ${esc(t.kind)}</div>
      <div class="k">依赖</div><div class="mono small">${esc(t.depends_on || "[]")}</div>
      <div class="k">工作目录</div><div class="mono small">${esc(t.workspace || "")}</div>
      <div class="k">错误</div><div>${esc(t.error || "—")}</div>
    </div>
    <div class="section-title">运行记录</div>
    <table><tbody>${runs || '<tr><td class="muted">暂无</td></tr>'}</tbody></table>
    <div class="section-title">Prompt</div>
    <div class="json-view">${esc(t.prompt || "")}</div>`;
  $("#modal").classList.remove("hidden");
}

async function openRunLogs(id) {
  const d = await api(`/api/runs/${id}/logs`);
  $("#modal-title").textContent = "运行日志 · " + d.run.agent + " · " + d.run.status;
  $("#modal-body").innerHTML = `
    <div class="section-title" style="margin-top:0">stdout</div>
    <div class="log-view">${esc(d.stdout || "(空)")}</div>
    <div class="section-title">stderr</div>
    <div class="log-view">${esc(d.stderr || "(空)")}</div>`;
  $("#modal").classList.remove("hidden");
}

// ---------------- actions ----------------
document.addEventListener("click", async (ev) => {
  const nav = ev.target.closest(".nav-item");
  if (nav) { switchView(nav.dataset.view); return; }
  const el = ev.target.closest("[data-action]");
  if (!el) return;
  const act = el.dataset.action;
  const id = el.dataset.id;
  try {
    if (act === "goto") switchView(el.dataset.view);
    else if (act === "filter") { state.itemFilter = el.dataset.status; render(); }
    else if (act === "event-filter") { state.eventLevel = el.dataset.level; render(); }
    else if (act === "digest-date") { state.digestDate = el.dataset.date; render(); }
    else if (act === "digest-run") {
      state.busy = true;
      try {
        toast("正在抓取论文、Skills 与仓库…");
        const r = await api("/api/digest/run", { method: "POST", body: { notify: true } });
        state.digestDate = r.date || state.digestDate;
        toast(`已生成：论文 ${r.counts.papers} · 技能 ${r.counts.skills} · 仓库 ${r.counts.repos}`);
      } finally { state.busy = false; }
      await render();
    }
    else if (act === "detail") await openItem(id);
    else if (act === "task-detail") await openTask(id);
    else if (act === "run-logs") await openRunLogs(id);
    else if (act === "plan") { toast("正在规划…"); await api(`/api/items/${id}/plan`, { method: "POST", body: { use_llm: el.dataset.llm === "1" } }); toast("已生成子任务"); await render(); if (!$("#modal").classList.contains("hidden")) await openItem(id); }
    else if (act === "run") { toast("已加入执行队列"); await api(`/api/items/${id}/run`, { method: "POST", body: {} }); await render(); }
    else if (act === "archive") { await api(`/api/items/${id}/archive`, { method: "POST" }); toast("已归档"); $("#modal").classList.add("hidden"); await render(); }
    else if (act === "retry") { await api(`/api/tasks/${id}/retry`, { method: "POST" }); toast("已重新入队"); await render(); }
    else if (act === "cancel") { await api(`/api/tasks/${id}/cancel`, { method: "POST" }); toast("已取消"); await render(); }
    else if (act === "approve" || act === "reject") {
      await api(`/api/approvals/${id}/decision`, { method: "POST", body: { decision: act === "approve" ? "approved" : "rejected" } });
      toast(act === "approve" ? "已批准，任务重新入队" : "已驳回"); await render();
    } else if (act === "patrol-one") { toast("正在巡检 " + el.dataset.source + "…"); await api("/api/patrol", { method: "POST", body: { only: [el.dataset.source] } }); toast("巡检完成"); await render(); }
    else if (act === "ask") {
      const q = $("#ask-q").value.trim();
      if (!q) return toast("先写下问题", true);
      el.disabled = true;
      try {
        await api("/api/ask", { method: "POST", body: { question: q } });
        $("#ask-q").value = "";
        toast("已交给 DeepTutor，回答完成后可在事项里查看");
        await render();
      } catch (e) {
        toast("提问失败：" + (e.message || e), true);
      } finally {
        el.disabled = false;
      }
    } else if (act === "ctx-add") {
      const category = $("#ctx-cat").value, key = $("#ctx-key").value.trim(), value = $("#ctx-val").value.trim();
      if (!key || !value) return toast("键和值都不能为空", true);
      await api("/api/context", { method: "POST", body: { category, key, value } });
      toast("已保存"); await render();
    } else if (act === "ctx-del") {
      if (!confirm("删除这条上下文？")) return;
      await api("/api/context/" + encodeURIComponent(el.dataset.key), { method: "DELETE" });
      toast("已删除"); await render();
    }
  } catch (e) { toast(e.message, true); }
});

$("#btn-patrol").addEventListener("click", async () => {
  const btn = $("#btn-patrol"); btn.disabled = true; btn.textContent = "巡检中…";
  try { await api("/api/patrol", { method: "POST", body: {} }); toast("巡检完成"); await render(); }
  catch (e) { toast(e.message, true); }
  finally { btn.disabled = false; btn.textContent = "立即巡检"; }
});
$("#btn-refresh").addEventListener("click", render);
$("#modal-close").addEventListener("click", () => $("#modal").classList.add("hidden"));
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") $("#modal").classList.add("hidden"); });

function poll() {
  if (!document.hidden) render();
}
state.timer = setInterval(poll, 4000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) render(); });

api("/api/health").then(() => setHealth(true)).catch(() => setHealth(false));
api("/api/overview").then((d) => { $("#version-text").textContent = "v" + d.version; }).catch(() => {});
switchView("overview");
