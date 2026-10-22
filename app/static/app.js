const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

const state = { me: null, page: 1, size: 12, total: 0, status: "", query: "", current: null, files: [] };

const VIEW_META = {
  workbench: ["数字员工工作台", "今日任务与执行质量"],
  claims: ["报销单", "AI 结论与证据链"],
  submit: ["提交报销", "上传票据，自动预审"],
  policies: ["政策与规则", "数字员工的判断依据"],
  audit: ["审计日志", "操作留痕与合规追溯"]
};

const STATUS = {
  auto_passed: ["已自动通过", "green"], pending_review: ["待人工审批", "amber"],
  approved: ["已审批通过", "teal"], rejected: ["已驳回", "red"],
  processing: ["预审中", "violet"], submitted: ["待预审", "gray"], failed: ["处理失败", "red"]
};

const DECISION = { pass: ["建议通过", "pass"], review: ["建议人工复核", "review"], reject: ["建议退回", "reject"] };
const KIND_LABEL = { hotel: "住宿", train: "铁路", air: "机票", taxi: "市内交通", meal: "餐饮", entertainment: "业务招待", invoice: "其他" };

async function api(path, options = {}) {
  const resp = await fetch(path, { credentials: "same-origin", ...options });
  if (resp.status === 401) { location.href = "/login"; throw new Error("未登录"); }
  const type = resp.headers.get("content-type") || "";
  const data = type.includes("application/json") ? await resp.json() : await resp.text();
  if (!resp.ok) throw new Error((data && data.detail) || "请求失败");
  return data;
}

function toast(msg, isError = false) {
  const el = document.createElement("div");
  el.className = "toast" + (isError ? " err" : "");
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .3s"; }, 2800);
  setTimeout(() => el.remove(), 3200);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function money(v) { return Number(v || 0).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
function fmtDate(s) { return s ? String(s).replace("T", " ").slice(0, 16) : "—"; }
function statusBadge(status) { const [label, cls] = STATUS[status] || [status, "gray"]; return `<span class="badge ${cls}">${esc(label)}</span>`; }
function decisionMeta(d) { return DECISION[d] || ["待预审", "idle"]; }

function route() {
  const name = (location.hash || "#workbench").slice(1);
  const view = VIEW_META[name] ? name : "workbench";
  $$(".view").forEach(v => v.classList.toggle("active", v.id === `view-${view}`));
  $$("#nav a").forEach(a => a.classList.toggle("active", a.dataset.view === view));
  $("#pageTitle").textContent = VIEW_META[view][0];
  $("#pageCrumb").textContent = VIEW_META[view][1];
  if (view === "workbench") loadWorkbench();
  if (view === "claims") loadClaims();
  if (view === "policies") loadPolicies();
  if (view === "audit") loadAudit();
  if (view === "submit") setTimeout(() => $("#fPurpose").focus(), 100);
}

async function boot() {
  try { state.me = await api("/api/me"); }
  catch (e) { location.href = "/login"; return; }
  $("#userName").textContent = state.me.display_name;
  $("#userRole").textContent = `${state.me.role_label} · ${state.me.department || "—"}`;
  $("#avatar").textContent = state.me.display_name.slice(0, 1);
  const llmLabel = state.me.llm ? (state.me.llm_model || "LLM") + " 已接入" : "规则模式（未接入 LLM）";
  $("#thresholdText").textContent = `${llmLabel} · 自动通过阈值 ${state.me.auto_pass_threshold.toFixed(2)}`;
  $("#modelChip").title = state.me.llm
    ? `生成端：${state.me.llm_model}；硬规则仍由确定性规则引擎判定`
    : "未配置 LLM，审核意见使用模板生成";
  $$("[data-roles]").forEach(el => {
    if (!el.dataset.roles.split(",").includes(state.me.role)) el.style.display = "none";
  });
  route();
}

async function loadWorkbench() {
  const s = await api("/api/stats");
  $("#statCards").innerHTML = `
    <div class="stat accent"><div class="k">今日处理</div><div class="v">${s.total}<small>单</small></div><div class="d">其中 ${s.pending} 单待人工处理</div></div>
    <div class="stat"><div class="k">自动通过率</div><div class="v">${s.auto_rate}<small>%</small></div><div class="d">${s.auto_passed} 单无需人工介入</div></div>
    <div class="stat"><div class="k">已核准金额</div><div class="v">${money(s.approved_amount)}<small>元</small></div><div class="d">自动通过 ${money(s.auto_amount)} 元</div></div>
    <div class="stat"><div class="k">规则命中</div><div class="v">${s.checks.fail + s.checks.warn}<small>项</small></div><div class="d">阻断 ${s.checks.fail} · 提示 ${s.checks.warn}</div></div>`;

  const list = await api("/api/claims?status=pending_review&size=6");
  if (!list.items.length) {
    $("#pendingList").innerHTML = `<div class="empty"><b>没有待办</b>数字员工已处理完当前队列</div>`;
  } else {
    $("#pendingList").innerHTML = list.items.map(claimRowHtml).join("");
    $$("#pendingList .claim-row").forEach(el => el.addEventListener("click", () => openClaim(Number(el.dataset.id))));
  }

  const total = Math.max(1, s.checks.fail + s.checks.warn);
  $("#ruleBars").innerHTML = s.top_rules.map(r => `
    <div class="bar-row"><span class="name">${esc(r.rule_name)}</span>
      <div class="bar-track"><div class="bar-fill ${r.status === 'fail' ? 'danger' : 'amber'}" style="width:${Math.min(100, (r.n / total) * 100)}%"></div></div>
      <span class="val">${r.n} 次</span></div>`).join("") || `<p class="hint">暂无命中</p>`;
  $("#ruleNote").textContent = `规则先做确定性判断（金额、日期、抬头、重复），模型只负责需要语义理解的环节。当前 ${s.checks.pass} 项通过、${s.checks.warn} 项提示、${s.checks.fail} 项阻断。`;

  $("#recentList").innerHTML = s.recent.slice(0, 5).map(c => {
    const [label] = decisionMeta(c.ai_decision);
    return `<div class="claim-row" data-id="${c.id}" style="grid-template-columns:38px 1fr 112px 90px;padding:10px 12px">
      <div class="claim-ico ${c.ai_decision || 'idle'}">${c.ai_decision === 'pass' ? '✓' : c.ai_decision === 'reject' ? '!' : '?'}</div>
      <div class="claim-main"><b>${esc(c.purpose)}</b><span><span class="no">${esc(c.claim_no)}</span>${esc(c.applicant_name)} · ${esc(c.destination || '—')}</span></div>
      <div class="claim-money"><b>${money(c.claimed_amount)}</b><span>${label}</span></div>
      <div style="text-align:right">${statusBadge(c.status)}</div>
    </div>`;
  }).join("") || `<div class="empty"><b>暂无记录</b></div>`;
  $$("#recentList .claim-row").forEach(el => el.addEventListener("click", () => openClaim(Number(el.dataset.id))));

  const trend = s.trend.length ? s.trend : [{ d: "—", n: 0 }];
  const tmax = Math.max(1, ...trend.map(t => t.n));
  $("#trendChart").innerHTML = trend.map(t => `
    <div style="flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;gap:6px;height:100%">
      <span style="font-size:10px;font-family:var(--mono);color:var(--muted)">${t.n}</span>
      <div style="width:100%;max-width:38px;height:${Math.max(6, (t.n / tmax) * 104)}px;background:linear-gradient(180deg,#c96a4f,#b0432b);border-radius:4px 4px 2px 2px"></div>
      <span style="font-size:9.5px;color:var(--muted-2)">${esc(String(t.d || '').slice(5))}</span>
    </div>`).join("");
}

function claimRowHtml(c) {
  const [label] = decisionMeta(c.ai_decision);
  const flags = [];
  if (c.fails) flags.push(`<span class="badge red">阻断 ${c.fails}</span>`);
  if (c.warns) flags.push(`<span class="badge amber">提示 ${c.warns}</span>`);
  if (!c.fails && !c.warns) flags.push(`<span class="badge green">无异常</span>`);
  return `<div class="claim-row" data-id="${c.id}">
    <div class="claim-ico ${c.ai_decision || 'idle'}">${c.ai_decision === 'pass' ? '✓' : c.ai_decision === 'reject' ? '!' : c.ai_decision === 'review' ? '?' : '·'}</div>
    <div class="claim-main"><b>${esc(c.purpose)}</b>
      <span><span class="no">${esc(c.claim_no)}</span>${esc(c.applicant_name)} · ${esc(c.destination || '—')} · ${c.files || 0} 份票据</span></div>
    <div class="claim-money"><b>¥${money(c.claimed_amount)}</b><span>可报 ${money(c.approved_amount)}</span></div>
    <div class="claim-flag">${flags.join("")}<span class="badge gray">${label}</span></div>
    <div class="claim-time">${fmtDate(c.updated_at)}</div>
    <div class="chev">›</div>
  </div>`;
}

async function loadClaims() {
  state.query = $("#claimQuery").value.trim();
  state.status = $("#claimStatus").value;
  const params = new URLSearchParams({ page: state.page, size: state.size });
  if (state.status) params.set("status", state.status);
  if (state.query) params.set("q", state.query);
  const list = $("#claimList");
  if (!list.children.length) list.innerHTML = Array(4).fill(`<div class="skeleton" style="height:66px"></div>`).join("");
  const data = await api(`/api/claims?${params}`);
  state.total = data.total;
  if (!data.items.length) {
    list.innerHTML = `<div class="empty"><b>没有匹配的报销单</b>调整筛选条件，或提交一张新的报销单</div>`;
    $("#claimPager").innerHTML = "";
    return;
  }
  list.innerHTML = data.items.map(claimRowHtml).join("");
  $$("#claimList .claim-row").forEach(el => el.addEventListener("click", () => openClaim(Number(el.dataset.id))));
  const pages = Math.max(1, Math.ceil(state.total / state.size));
  $("#claimPager").innerHTML = `
    <button ${state.page <= 1 ? "disabled" : ""} id="prevPage">上一页</button>
    <span>第 ${state.page} / ${pages} 页 · 共 ${state.total} 单</span>
    <button ${state.page >= pages ? "disabled" : ""} id="nextPage">下一页</button>`;
  const prev = $("#prevPage"), next = $("#nextPage");
  if (prev) prev.addEventListener("click", () => { state.page--; loadClaims(); });
  if (next) next.addEventListener("click", () => { state.page++; loadClaims(); });
}

async function openClaim(id) {
  const c = await api(`/api/claims/${id}`);
  state.current = c;
  $("#dTitle").textContent = c.purpose;
  $("#dSub").innerHTML = `<span style="font-family:var(--mono);color:var(--accent)">${esc(c.claim_no)}</span>
    <span>${esc(c.applicant_name)} · ${esc(c.department || '—')}</span>
    <span>${esc(c.destination || '—')}</span>
    <span>${c.documents.length} 份票据</span>${statusBadge(c.status)}`;

  $("#dMeta").innerHTML = [
    ["费用类型", c.category], ["行程", c.trip_start ? `${c.trip_start} 至 ${c.trip_end || '—'}` : "未填写"],
    ["目的地", c.destination || "—"], ["申报金额", `¥${money(c.claimed_amount)}`, "mono"],
    ["可报金额", `¥${money(c.approved_amount)}`, "mono"], ["提交时间", fmtDate(c.submitted_at)],
    ["AI 置信度", `${Math.round((c.ai_confidence || 0) * 100)}%`]
  ].map(([k, v, cls]) => `<div class="meta-row"><span class="k">${k}</span><span class="v ${cls || ''}">${esc(v)}</span></div>`).join("");

  $("#dItems").innerHTML = c.items.length ? `
    <table class="item-table">
      <thead><tr><th>日期</th><th>类别</th><th>商户</th><th style="text-align:right">金额</th></tr></thead>
      <tbody>${c.items.map(i => `<tr><td>${esc(i.occurred_on || '—')}</td><td>${esc(i.category)}</td>
        <td title="${esc(i.merchant)}">${esc((i.merchant || '—').slice(0, 12))}</td>
        <td class="num">${money(i.amount)}</td></tr>`).join("")}</tbody>
    </table>` : `<p class="hint" style="font-size:11.5px;color:var(--muted-2)">未识别到费用明细</p>`;

  $("#dDocs").innerHTML = c.documents.map(d => `
    <div class="doc-card" data-doc="${d.id}">
      <span class="doc-kind">${KIND_LABEL[d.kind] || d.kind}${d.method === 'ocr' ? ' · OCR' : ''}</span>
      <b title="${esc(d.original_name)}">${esc(d.original_name)}</b>
      <span>¥${money((d.fields || {}).total)} · 置信 ${Math.round((d.confidence || 0) * 100)}%</span>
    </div>`).join("") || `<p class="hint">未上传票据</p>`;
  $$("#dDocs .doc-card").forEach(el => el.addEventListener("click", () => {
    window.open(`/api/claims/${c.id}/documents/${el.dataset.doc}/file`, "_blank");
  }));

  const trace = (c.task && c.task.trace) ? c.task.trace : [];
  $("#dTrace").innerHTML = trace.length ? `<div class="trace">${trace.map(t => `
    <div class="trace-item ${t.status === 'done' ? 'done' : t.status === 'failed' ? 'failed' : 'running'}">
      <div class="trace-dot">${t.status === 'done' ? '✓' : t.status === 'failed' ? '!' : '·'}</div>
      <div class="trace-body"><b>${esc(t.stage)}</b><span>${esc(t.detail)}</span><time>${fmtDate(t.at)}</time></div>
    </div>`).join("")}</div>` : `<p class="hint" style="font-size:11.5px;color:var(--muted-2)">暂无执行轨迹</p>`;

  renderDecision(c);
  renderChecks(c);
  renderApprovals(c);

  const canDecide = c.can_decide && ["pending_review", "processing", "submitted"].includes(c.status);
  $("#approveBar").style.display = canDecide ? "flex" : "none";
  if (canDecide) $("#approveAmount").value = (c.ai_decision === "reject" ? 0 : (c.approved_amount || c.claimed_amount || 0)).toFixed(2);
  $("#dRerun").style.display = c.can_decide ? "grid" : "none";
  $("#drawer").removeAttribute("inert");
  $("#drawerMask").classList.add("open");
  $("#drawer").classList.add("open");
}

function renderDecision(c) {
  const [label, cls] = decisionMeta(c.ai_decision);
  const conf = Math.round((c.ai_confidence || 0) * 100);
  const counts = { fail: 0, warn: 0, pass: 0 };
  c.checks.forEach(k => { counts[k.status] = (counts[k.status] || 0) + 1; });
  $("#dDecision").innerHTML = `
    <div class="ai-box">
      <div class="ai-head">
        <div class="avatar" style="width:26px;height:26px;font-size:10px">审</div>
        <div><div class="who">数字员工 · 小审</div><div class="role">报销预审岗位 · 校验 ${c.checks.length} 项</div></div>
        <span style="margin-left:auto;font-size:10.5px;color:var(--muted-2)">${fmtDate(c.updated_at)}</span>
      </div>
      <div class="ai-body">
        <div class="ai-line">
          <span class="decision-pill ${cls}">${label}</span>
          <span style="font-size:11px;color:var(--muted-2)">置信度 ${conf}%</span>
          <div class="conf-bar"><div style="width:${conf}%"></div></div>
        </div>
        <div class="ai-summary">${esc(c.ai_summary || "尚未生成预审意见")}</div>
        <div style="margin-top:8px;font-size:10.5px;color:var(--muted-2)">
          意见来源：${c.ai_source === "llm" ? `大模型生成（${esc(state.me.llm_model || "LLM")}）` : "规则模板"}
        </div>
        <div class="amount-compare">
          <div><span>申报金额</span><b>¥${money(c.claimed_amount)}</b></div>
          <div><span>${c.ai_decision === "reject" ? "建议可报" : "建议可报"}</span><b style="color:${c.ai_decision === "reject" ? "var(--danger)" : "var(--teal)"}">${c.ai_decision === "reject" ? "退回补正" : "¥" + money(c.approved_amount)}</b></div>
          <div><span>校验结果</span><b>${counts.fail} 阻断 / ${counts.warn} 提示</b></div>
        </div>
      </div>
    </div>`;
}

function renderChecks(c) {
  if (!c.checks.length) { $("#dChecks").innerHTML = `<div class="empty"><b>暂无校验结果</b></div>`; return; }
  $("#dChecks").innerHTML = c.checks.map(k => `
    <div class="check ${k.status}">
      <div class="check-top">
        <span class="badge ${k.status === 'fail' ? 'red' : k.status === 'warn' ? 'amber' : 'green'}">
          ${k.status === 'fail' ? '阻断' : k.status === 'warn' ? '提示' : '通过'}</span>
        <b>${esc(k.title)}</b>
        <span class="rule-code">${esc(k.rule_code)}</span>
      </div>
      ${k.detail ? `<div class="check-detail">${esc(k.detail)}</div>` : ""}
      ${k.evidence ? `<div class="check-evidence">证据：${esc(k.evidence)}</div>` : ""}
      <div class="check-policy">
        ${k.policy_title ? `依据《${esc(k.policy_title)}》${k.policy_code ? `（${esc(k.policy_code)}）` : ''}：${esc(k.policy_clause || '')}` : '未匹配到具体条款'}
        ${k.suggestion ? `<br>建议：${esc(k.suggestion)}` : ""}
      </div>
    </div>`).join("");
  $$("#dChecks .check").forEach(el => el.addEventListener("click", () => el.classList.toggle("open")));
}

function renderApprovals(c) {
  if (!c.approvals.length) { $("#dApprovals").innerHTML = ""; return; }
  $("#dApprovals").innerHTML = `<div class="divider"></div><div class="side-title">人工处理记录</div>
    ${c.approvals.map(a => `<div class="check" style="cursor:default">
      <div class="check-top">
        <span class="badge ${a.action === 'approve' ? 'teal' : 'red'}">${a.action === 'approve' ? '通过' : '驳回'}</span>
        <b>${esc(a.approver_name)}</b>
        <span style="margin-left:auto;font-size:10px;color:var(--muted-2)">${fmtDate(a.created_at)}</span>
      </div>
      <div class="check-detail">核准金额 ¥${money(a.approved_amount)}${a.comment ? ` · ${esc(a.comment)}` : ""}</div>
      ${a.ai_decision ? `<div class="check-detail" style="color:var(--muted-2)">AI 原结论：${esc(decisionMeta(a.ai_decision)[0])}</div>` : ""}
    </div>`).join("")}`;
}

async function decide(action) {
  const c = state.current;
  if (!c) return;
  const amount = Number($("#approveAmount").value || 0);
  const comment = $("#approveComment").value.trim();
  try {
    await api(`/api/claims/${c.id}/decision`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, approved_amount: amount, comment })
    });
    toast(action === "approve" ? "已通过，处理记录已留痕" : "已驳回，处理记录已留痕");
    await openClaim(c.id);
    loadWorkbench();
    if ($("#view-claims").classList.contains("active")) loadClaims();
  } catch (e) { toast(e.message, true); }
}

async function rerun() {
  const c = state.current;
  if (!c) return;
  try {
    await api(`/api/claims/${c.id}/rerun`, { method: "POST" });
    toast("已重新交给数字员工");
    setTimeout(async () => { await openClaim(c.id); loadWorkbench(); }, 900);
  } catch (e) { toast(e.message, true); }
}

function closeDrawer() {
  $("#drawerMask").classList.remove("open");
  $("#drawer").classList.remove("open");
  $("#drawer").setAttribute("inert", "");
  state.current = null;
}

async function loadPolicies(q = "") {
  const data = await api(`/api/policies${q ? `?q=${encodeURIComponent(q)}` : ""}`);
  $("#policyList").innerHTML = data.items.map(p => `
    <div class="policy">
      <div class="policy-head">
        <span class="rule-code">${esc(p.code || "")}</span>
        <b>${esc(p.title)}</b>
        ${p.category ? `<span class="badge gray">${esc(p.category)}</span>` : ""}
        <button class="toggle">展开</button>
      </div>
      <div class="policy-body">${esc(p.clause || p.content || "")}</div>
    </div>`).join("") || `<div class="empty"><b>没有匹配的条款</b></div>`;
  $$("#policyList .policy .toggle").forEach(btn => btn.addEventListener("click", e => {
    const box = e.target.closest(".policy");
    box.classList.toggle("open");
    e.target.textContent = box.classList.contains("open") ? "收起" : "展开";
  }));
}

async function loadAudit() {
  const rows = await api("/api/audit?limit=100");
  $("#auditTable").innerHTML = `
    <thead><tr><th>时间</th><th>用户</th><th>操作</th><th>对象</th><th>详情</th><th>IP</th></tr></thead>
    <tbody>${rows.map(r => `<tr>
      <td style="font-family:var(--mono);font-size:10.5px">${fmtDate(r.created_at)}</td>
      <td>${esc(r.username || "—")}</td><td>${esc(r.action)}</td>
      <td>${esc(r.target_type)}${r.target_id ? " #" + r.target_id : ""}</td>
      <td style="max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.detail || "")}</td>
      <td style="font-family:var(--mono);font-size:10.5px">${esc(r.ip || "")}</td>
    </tr>`).join("")}</tbody>`;
}

function renderFiles() {
  $("#fileChips").innerHTML = state.files.map((f, i) => `
    <span class="file-chip"><code>${esc(f.name)}</code>
      <button data-i="${i}" style="border:none;background:none;color:var(--danger);cursor:pointer">×</button></span>`).join("");
  $$("#fileChips button").forEach(b => b.addEventListener("click", () => {
    state.files.splice(Number(b.dataset.i), 1);
    renderFiles();
  }));
}

async function submitClaim() {
  const purpose = $("#fPurpose").value.trim();
  if (!purpose) { toast("请填写报销事由", true); return; }
  if (!state.files.length) { toast("请至少上传一份票据", true); return; }
  const form = new FormData();
  form.append("purpose", purpose);
  form.append("trip_start", $("#fStart").value);
  form.append("trip_end", $("#fEnd").value);
  form.append("destination", $("#fDest").value.trim());
  form.append("category", $("#fCategory").value);
  form.append("claimed_amount", $("#fAmount").value || "0");
  state.files.forEach(f => form.append("files", f));
  const btn = $("#submitClaim");
  btn.disabled = true;
  btn.textContent = "正在解析票据…";
  try {
    const r = await api("/api/claims", { method: "POST", body: form });
    toast("已提交，数字员工正在预审");
    state.files = [];
    renderFiles();
    $("#fPurpose").value = "";
    $("#fAmount").value = "";
    setTimeout(async () => { await openClaim(r.claim_id); loadWorkbench(); }, 1200);
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "提交并交给数字员工";
  }
}

function bind() {
  $$("[data-goto]").forEach(b => b.addEventListener("click", () => { location.hash = "#" + b.dataset.goto; }));
  window.addEventListener("hashchange", route);
  $("#logout").addEventListener("click", async () => {
    await api("/api/auth/logout", { method: "POST" }).catch(() => {});
    location.href = "/login";
  });
  $("#refresh").addEventListener("click", () => route());
  $("#drawer").setAttribute("inert", "");
  $("#drawerMask").addEventListener("click", closeDrawer);
  $("#dClose").addEventListener("click", closeDrawer);
  $("#dRerun").addEventListener("click", rerun);
  $("#approveBtn").addEventListener("click", () => decide("approve"));
  $("#rejectBtn").addEventListener("click", () => decide("reject"));
  document.addEventListener("keydown", e => { if (e.key === "Escape") closeDrawer(); });

  let queryTimer = null;
  $("#claimQuery").addEventListener("input", () => {
    clearTimeout(queryTimer);
    queryTimer = setTimeout(() => { state.page = 1; loadClaims(); }, 300);
  });
  $("#claimStatus").addEventListener("change", () => { state.page = 1; loadClaims(); });
  $("#clearFilters").addEventListener("click", () => {
    $("#claimQuery").value = ""; $("#claimStatus").value = ""; state.page = 1; loadClaims();
  });

  const dz = $("#dropzone");
  dz.addEventListener("click", () => $("#fileInput").click());
  $("#fileInput").addEventListener("change", e => {
    state.files.push(...Array.from(e.target.files));
    renderFiles(); e.target.value = "";
  });
  ["dragenter", "dragover"].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.add("drag"); }));
  ["dragleave", "drop"].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.remove("drag"); }));
  dz.addEventListener("drop", e => {
    state.files.push(...Array.from(e.dataTransfer.files).filter(f => f.name.toLowerCase().endsWith(".pdf")));
    renderFiles();
  });
  $("#submitClaim").addEventListener("click", submitClaim);

  let policyTimer = null;
  $("#policyQuery").addEventListener("input", e => {
    clearTimeout(policyTimer);
    policyTimer = setTimeout(() => loadPolicies(e.target.value.trim()), 300);
  });
  $("#refreshPolicies").addEventListener("click", () => loadPolicies($("#policyQuery").value.trim()));
  $("#refreshAudit").addEventListener("click", loadAudit);

  const openMenu = () => { $("#sidebar").classList.add("open"); $("#sideMask").classList.add("open"); };
  const closeMenu = () => { $("#sidebar").classList.remove("open"); $("#sideMask").classList.remove("open"); };
  $("#menuBtn").addEventListener("click", openMenu);
  $("#sideMask").addEventListener("click", closeMenu);
  $$("#nav a").forEach(a => a.addEventListener("click", closeMenu));
}

bind();
boot();


