"""智能体编排层（最小可演示版）。

设计目标：在不破坏"确定性规则做决策"这一底线的前提下，让系统具备可被观察到的
Agent 智能性——主控 Agent 自主规划、按需调用工具、多 Agent 协作、复核 Agent 收敛、
高风险人工接管，全过程写入 trace 供前端可视化。

架构对齐 LangGraph 的 StateGraph：
    START → orchestrator(主控/Planner) ⇄ worker agents(工具节点) → reviewer(复核) → END
本文件实现一个等价形状的轻量状态图引擎（零新依赖），可无缝替换为真正的 LangGraph。

决策护栏（重要）：
    - 通过/复核/退回 的最终结论始终由 rules.summarize() 给出，模型不改变硬判定；
    - run_rules 是强制节点：即使 Planner 漏调，reviewer 也会兜底执行一次；
    - 未配置 LLM 时，orchestrator 退化为确定性计划，行为与改造前等价。
"""
from __future__ import annotations

import json
import re

from . import llm as llm_service
from . import policies as policy_service
from . import rules as rule_engine
from .rules import CheckResult

END = "__end__"
MAX_PLANNER_STEPS = 6          # Planner 最多自主决策轮数
MAX_GRAPH_STEPS = 40           # 状态图最大跳数，防死循环


# --------------------------------------------------------------------------- #
# 轻量状态图引擎（LangGraph StateGraph 形状）
# --------------------------------------------------------------------------- #
class StateGraph:
    def __init__(self) -> None:
        self._nodes: dict[str, callable] = {}
        self._edges: dict[str, str] = {}
        self._cond: dict[str, tuple[callable, dict]] = {}
        self._entry: str | None = None

    def add_node(self, name: str, fn) -> "StateGraph":
        self._nodes[name] = fn
        return self

    def add_edge(self, src: str, dst: str) -> "StateGraph":
        self._edges[src] = dst
        return self

    def add_conditional_edges(self, src: str, router, mapping: dict) -> "StateGraph":
        self._cond[src] = (router, mapping)
        return self

    def set_entry_point(self, name: str) -> "StateGraph":
        self._entry = name
        return self

    def compile(self) -> "CompiledGraph":
        return CompiledGraph(self)


class CompiledGraph:
    def __init__(self, g: StateGraph) -> None:
        self._g = g

    def run(self, state) -> None:
        cur = self._g._entry
        steps = 0
        while cur and cur != END:
            if steps > MAX_GRAPH_STEPS:
                break
            steps += 1
            self._g._nodes[cur](state)
            if cur in self._g._cond:
                router, mapping = self._g._cond[cur]
                key = router(state)
                cur = mapping.get(key, END)
            else:
                cur = self._g._edges.get(cur, END)


# --------------------------------------------------------------------------- #
# Agent 状态
# --------------------------------------------------------------------------- #
class AgentState:
    def __init__(self, claim, docs, items, conn, task_id, tracer) -> None:
        self.claim = claim
        self.docs = docs
        self.items = items
        self.conn = conn
        self.task_id = task_id
        self._tracer = tracer            # tracer(stage, detail, status, progress)

        self.checks: list[CheckResult] = []        # 规则引擎产出的校验项
        self.extra_checks: list[CheckResult] = []  # 工具 Agent 追加的校验项(source=agent)
        self.policy_evidence: list[dict] = []      # 检索到的政策条款
        self.observations: list[dict] = []         # [{tool, obs}] 供 Planner 决策
        self.tools_called: list[str] = []          # 已执行工具
        self.pending_tool: str | None = None       # Planner 本轮决定调用的工具
        self.plan_log: list[str] = []              # Planner 决策留痕
        self.decision: dict = {}
        self.opinion: str = ""
        self.ai_source: str = "template"
        self.needs_human: bool = False

    # 便捷：写执行轨迹
    def trace(self, stage, detail, status="running", progress=None):
        if self.task_id:
            self._tracer(self.conn, self.task_id, stage, detail, status, progress)

    def record(self, tool, obs):
        self.observations.append({"tool": tool, "obs": obs})
        if tool not in self.tools_called:
            self.tools_called.append(tool)


# --------------------------------------------------------------------------- #
# 工具（worker agents 背后的能力）
# --------------------------------------------------------------------------- #
TOOLS = {
    "run_rules": "运行 18 条确定性规则引擎，输出全部校验项与命中情况（决策依据，必跑）",
    "search_policy": "检索政策库条款，为当前最突出的问题找到可引用的政策原文（RAG 检索）",
    "check_history": "跨报销单分析申请人历史：疑似重复消费模式、历史退回/复核频次（规则引擎不具备）",
    "check_feedback": "挖掘人工审批对 AI 结论的历史修正：申请人被推翻次数、金额削减幅度、被反复推翻的规则（反馈闭环）",
}
# 无 LLM 时的确定性计划：保持与改造前一致，feedback 工具仅在 LLM 规划下可被调用
TOOL_ORDER = ["run_rules", "search_policy", "check_history"]


def tool_run_rules(state: AgentState) -> str:
    if state.checks:
        return "规则引擎已执行，跳过重复调用"
    checks = rule_engine.run_rules(state.claim, state.docs, state.items, {"conn": state.conn})
    state.checks = checks
    fails = [c for c in checks if c.status == "fail"]
    warns = [c for c in checks if c.status == "warn"]
    sem = [c for c in checks if c.code == "R020"]
    obs = (f"命中 {len(fails)} 阻断 / {len(warns)} 提示"
           + (f"；语义匹配(R020)：{sem[0].title}" if sem else ""))
    if fails:
        obs += "；主要问题：" + "、".join(c.title for c in (fails + warns)[:4])
    return obs


def tool_search_policy(state: AgentState) -> str:
    # 依据当前最突出的问题构造检索词；无问题时按报销类别检索
    fails = [c for c in state.checks if c.status == "fail"]
    warns = [c for c in state.checks if c.status == "warn"]
    if fails or warns:
        query = " ".join(c.title for c in (fails + warns)[:3])
    else:
        query = state.claim.get("category") or "差旅费 报销 标准"
    hits = policy_service.search_policies(query, limit=2)
    if not hits:
        return f"政策库未检索到与「{query}」相关的条款"
    state.policy_evidence = hits
    lines = [f"[{h['code']}] {h['title']}：{h['clause'][:60]}" for h in hits]
    return "检索到政策依据 → " + " | ".join(lines)


def tool_check_history(state: AgentState) -> str:
    """真实跨单分析工具：规则引擎只做单内校验，这里做申请人历史画像。"""
    conn = state.conn
    claim = state.claim
    app_id = claim.get("applicant_id")
    findings: list[str] = []
    advisory: list[CheckResult] = []

    if app_id:
        # 1) 疑似重复消费：其他单据中出现相同商户 + 相近金额（±1 元）
        cur_merch = {(i.get("merchant") or "").strip(): float(i.get("amount") or 0)
                     for i in state.items if (i.get("merchant") or "").strip()}
        if cur_merch:
            rows = conn.execute(
                "SELECT li.merchant, li.amount, c.claim_no FROM line_items li "
                "JOIN claims c ON c.id = li.claim_id "
                "WHERE c.applicant_id = ? AND c.id != ?", (app_id, claim["id"])).fetchall()
            for r in rows:
                m = (r["merchant"] or "").strip()
                if m in cur_merch and abs(float(r["amount"] or 0) - cur_merch[m]) <= 1.0:
                    findings.append(f"商户「{m}」金额 {float(r['amount']):.2f} 已在 {r['claim_no']} 出现")
            if findings:
                advisory.append(CheckResult(
                    "A001", "历史重复消费", "warn", "medium",
                    "疑似重复消费模式",
                    "；".join(findings[:3]),
                    f"申请人历史单据存在相同商户与相近金额",
                    "请核实是否为同一笔费用重复报销",
                    source="agent", confidence=0.6,
                ))

        # 2) 历史退回/复核频次
        hist = conn.execute(
            "SELECT ai_decision, COUNT(*) c FROM claims "
            "WHERE applicant_id = ? AND id != ? AND ai_decision IN ('reject','review') "
            "GROUP BY ai_decision", (app_id, claim["id"])).fetchall()
        bad = sum(int(h["c"]) for h in hist)
        if bad >= 2:
            findings.append(f"该申请人历史有 {bad} 单被退回/复核")
            advisory.append(CheckResult(
                "A002", "历史风险画像", "warn", "low",
                "申请人历史预审风险偏高",
                f"历史累计 {bad} 单进入退回/复核",
                "", "建议对本单加强人工复核",
                source="agent", confidence=0.55,
            ))

    if advisory:
        state.extra_checks.extend(advisory)
    obs = "；".join(findings) if findings else "未发现历史重复消费或异常风险画像"
    return obs


def tool_check_feedback(state: AgentState) -> str:
    """反馈闭环工具：从人工审批记录里挖"人对 AI 的修正"，形成画像反哺预审。"""
    conn = state.conn
    claim = state.claim
    app_id = claim.get("applicant_id")
    findings: list[str] = []
    advisory: list[CheckResult] = []

    # 1) 申请人维度：AI 建议复核/退回，但人工最终放行 → 推翻；人工削减金额 → 校准
    if app_id:
        rows = conn.execute(
            "SELECT a.action, a.ai_decision, a.approved_amount AS human_amount, "
            "       c.approved_amount AS ai_amount, c.claim_no "
            "FROM approvals a JOIN claims c ON c.id = a.claim_id "
            "WHERE c.applicant_id = ? AND c.id != ?", (app_id, claim["id"])).fetchall()
        overrides = [r for r in rows if str(r["ai_decision"]) in ("review", "reject")
                     and r["action"] == "approve"]
        cut_rows = [r for r in rows if r["action"] == "approve"
                    and r["human_amount"] is not None and r["ai_amount"]
                    and float(r["human_amount"]) < float(r["ai_amount"]) - 0.01]
        if overrides:
            findings.append(
                f"该申请人历史有 {len(overrides)} 单 AI 建议复核/退回但人工仍放行")
            advisory.append(CheckResult(
                "A003", "AI-人工分歧画像", "warn", "low",
                "本申请人存在 AI 结论被人工推翻记录",
                f"历史 {len(overrides)} 次人工放行覆盖了 AI 复核/退回建议"
                + (f"；人工平均核减金额 {sum(float(r['ai_amount']) - float(r['human_amount']) for r in cut_rows) / len(cut_rows):.2f} 元" if cut_rows else ""),
                "参考人工尺度把握复核力度", "如问题轻微可参考历史人工口径从宽处理",
                source="agent", confidence=0.5,
            ))
        elif cut_rows:
            avg_cut = sum(float(r["ai_amount"]) - float(r["human_amount"]) for r in cut_rows) / len(cut_rows)
            findings.append(f"人工对该申请人 {len(cut_rows)} 单平均核减 {avg_cut:.2f} 元")

    # 2) 规则维度：哪些 fail 规则被人工反复推翻 → 规则过严的治理信号
    ruled = conn.execute(
        "SELECT ch.rule_code, ch.rule_name, COUNT(*) c FROM approvals a "
        "JOIN claims k ON k.id = a.claim_id "
        "JOIN checks ch ON ch.claim_id = a.claim_id "
        "WHERE a.action = 'approve' AND ch.status = 'fail' "
        "AND ch.rule_code LIKE 'R%' "
        "GROUP BY ch.rule_code HAVING c >= 2").fetchall()
    if ruled:
        top = ", ".join(f"{r['rule_code']} {r['rule_name']}({r['c']}次)" for r in ruled[:3])
        findings.append(f"存在被人工放行覆盖的阻断规则：{top}")
        advisory.append(CheckResult(
            "A004", "规则误报信号", "warn", "low",
            "部分规则被人工审批反复推翻，可能过严",
            top,
            "数据来源：历史 approvals 与 checks 交叉统计",
            "建议评估相应规则的阈值或豁免条件",
            source="agent", confidence=0.5,
        ))

    if advisory:
        state.extra_checks.extend(advisory)
    obs = "；".join(findings) if findings else "暂无人工修正记录可参考（反馈数据随审批积累）"
    return obs


TOOL_FN = {
    "run_rules": tool_run_rules,
    "search_policy": tool_search_policy,
    "check_history": tool_check_history,
    "check_feedback": tool_check_feedback,
}


# --------------------------------------------------------------------------- #
# 主控 Agent（Orchestrator / Planner）
# --------------------------------------------------------------------------- #
def _planner_prompt(state: AgentState) -> str:
    claim = state.claim
    tool_desc = "\n".join(f"- {name}: {desc}" for name, desc in TOOLS.items())
    if state.observations:
        done = "\n".join(f"- 已调用 {o['tool']} → {o['obs'][:120]}" for o in state.observations)
    else:
        done = "（尚未调用任何工具）"
    return f"""你是财务共享中心的报销预审主控 Agent。请根据报销单情况，决定下一步调用哪个工具，或结束规划。

【报销单】
单号：{claim.get('claim_no')}｜事由：{claim.get('purpose') or '—'}｜类别：{claim.get('category') or '—'}
目的地：{claim.get('destination') or '—'}｜申报金额：{float(claim.get('claimed_amount') or 0):.2f} 元｜票据数：{len(state.docs)}

【可用工具】
{tool_desc}
- finish: 结束规划，交给复核 Agent 出结论

【已执行的工具与观察】
{done}

规划原则：
1. run_rules 是决策基础，必须先执行；
2. 出现问题或需要政策依据时，调用 search_policy 检索条款；
3. 需要判断是否重复报销/申请人风险时，调用 check_history；
4. 结论存在争议（如规则命中但情节轻微、或历史上人工常放行）时，调用 check_feedback 查看人工修正画像；
5. 信息足够即 finish，不要重复调用同一工具。

只输出一行，格式严格为：
ACTION: <工具名或 finish>"""


def node_orchestrator(state: AgentState) -> None:
    """主控 Agent：LLM 可用则 ReAct 式自主决策，否则走确定性计划。"""
    # 达到步数上限 → 结束规划
    if len(state.observations) >= MAX_PLANNER_STEPS:
        state.pending_tool = None
        state.trace("主控Agent", "已达最大规划步数，结束规划", progress=70)
        return

    if llm_service.available():
        try:
            raw = llm_service.chat(
                [{"role": "user", "content": _planner_prompt(state)}],
                temperature=0.0, max_tokens=20, timeout=20,
            )
            m = re.search(r"ACTION:\s*([a-zA-Z_]+)", raw)
            action = m.group(1) if m else "finish"
        except Exception as exc:
            action = "finish"
            state.plan_log.append(f"Planner 调用失败降级 finish：{str(exc)[:80]}")
        if action == "finish" or action not in TOOL_FN or action in state.tools_called:
            # 模型选择结束，或给了无效/重复动作
            if action in TOOL_FN and action in state.tools_called:
                state.plan_log.append(f"Planner 想重复调用 {action}，改为结束")
            state.pending_tool = None
            state.trace("主控Agent", f"规划结束，已调用工具：{('、'.join(state.tools_called) or '无')}", progress=70)
            return
        state.pending_tool = action
        state.plan_log.append(f"主控Agent 自主决定调用工具：{action}")
        state.trace("主控Agent", f"决策 → 调用工具【{action}】（{TOOLS[action][:20]}…）", progress=None)
        return

    # 无 LLM：确定性兜底计划（跳过已执行的）
    for name in TOOL_ORDER:
        if name not in state.tools_called:
            state.pending_tool = name
            state.plan_log.append(f"确定性计划调用：{name}")
            state.trace("主控Agent", f"按预设计划调用工具【{name}】", progress=None)
            return
    state.pending_tool = None
    state.trace("主控Agent", f"预设计划执行完毕：{'、'.join(state.tools_called)}", progress=70)


def route_orchestrator(state: AgentState) -> str:
    return state.pending_tool or "reviewer"


# --------------------------------------------------------------------------- #
# Worker Agent 节点：执行主控决定的工具，再回到主控
# --------------------------------------------------------------------------- #
def _make_worker(tool_name: str, agent_label: str, progress: int):
    def worker(state: AgentState) -> None:
        try:
            obs = TOOL_FN[tool_name](state)
        except Exception as exc:
            obs = f"工具执行异常：{str(exc)[:120]}"
        state.record(tool_name, obs)
        state.trace(agent_label, f"[{tool_name}] {obs}", progress=progress)
        state.pending_tool = None
    return worker


# --------------------------------------------------------------------------- #
# 复核 Agent（Reviewer）：收敛决策 + 生成意见 + 人工接管判定
# --------------------------------------------------------------------------- #
def node_reviewer(state: AgentState) -> None:
    # 护栏：规则必须跑过
    if not state.checks:
        state.trace("复核Agent", "护栏触发：Planner 未执行规则引擎，强制补跑", progress=72)
        tool_run_rules(state)
        state.record("run_rules", "由复核 Agent 护栏强制补跑")

    all_checks = state.checks + state.extra_checks
    decision = rule_engine.summarize(state.claim, all_checks, state.docs, state.items)

    # LLM 只负责表达，不改变决策
    ai_source = "template"
    if llm_service.available():
        comment, source = llm_service.generate_review_comment(state.claim, all_checks, state.items)
        if source == "llm" and comment:
            decision["summary"] = comment
            ai_source = "llm"
            state.trace("复核Agent", f"大模型生成审核意见（{len(comment)} 字），决策仍由规则给出", progress=88)
        else:
            state.trace("复核Agent", "模型调用失败，降级为模板意见", progress=88)

    # 人工接管判定：高风险 / 退回 / 低置信 → 强制人工审批
    needs_human = (
        decision["decision"] in ("reject", "review")
        or decision["risk_level"] == "high"
        or decision["confidence"] < rule_engine.config.REVIEW_CONFIDENCE
        or any(c.source == "agent" and c.status == "warn" for c in state.extra_checks)
    )
    state.needs_human = needs_human
    state.decision = decision
    state.opinion = decision["summary"]
    state.ai_source = ai_source

    takeover = "，转人工审批接管" if needs_human else "，可自动通过"
    state.trace(
        "复核Agent",
        f"结论 {decision['decision']}｜置信度 {decision['confidence']:.2f}｜"
        f"可报 {decision['approved_amount']:.2f} 元{takeover}",
        progress=90,
    )


# --------------------------------------------------------------------------- #
# 构图 + 入口
# --------------------------------------------------------------------------- #
def build_graph() -> CompiledGraph:
    g = StateGraph()
    g.add_node("orchestrator", node_orchestrator)
    g.add_node("w_rules", _make_worker("run_rules", "规则Agent", 62))
    g.add_node("w_policy", _make_worker("search_policy", "政策Agent", 66))
    g.add_node("w_history", _make_worker("check_history", "风控Agent", 68))
    g.add_node("w_feedback", _make_worker("check_feedback", "反馈Agent", 69))
    g.add_node("reviewer", node_reviewer)

    g.set_entry_point("orchestrator")
    g.add_conditional_edges("orchestrator", route_orchestrator, {
        "run_rules": "w_rules",
        "search_policy": "w_policy",
        "check_history": "w_history",
        "check_feedback": "w_feedback",
        "reviewer": "reviewer",
    })
    g.add_edge("w_rules", "orchestrator")
    g.add_edge("w_policy", "orchestrator")
    g.add_edge("w_history", "orchestrator")
    g.add_edge("w_feedback", "orchestrator")
    g.add_edge("reviewer", END)
    return g.compile()


_GRAPH = build_graph()


def run_review(claim, docs, items, conn, task_id, tracer) -> dict:
    """执行多 Agent 预审编排，返回 checks / decision / opinion / ai_source / plan。

    tracer 签名：tracer(conn, task_id, stage, detail, status, progress)
    """
    state = AgentState(claim, docs, items, conn, task_id, tracer)
    state.trace("主控Agent", f"启动多 Agent 编排（LLM {'已接入' if llm_service.available() else '未接入，走确定性计划'}）",
                progress=45)
    _GRAPH.run(state)
    return {
        "checks": state.checks + state.extra_checks,
        "decision": state.decision,
        "opinion": state.opinion,
        "ai_source": state.ai_source,
        "needs_human": state.needs_human,
        "plan_log": state.plan_log,
        "tools_called": state.tools_called,
        "policy_evidence": state.policy_evidence,
    }
