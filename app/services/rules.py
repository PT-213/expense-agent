"""报销预审规则引擎。

原则：确定性规则先跑，模型不参与金额/日期判定；每条结论带证据与政策条款。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .. import config
from .policies import search_policies


@dataclass
class CheckResult:
    code: str
    name: str
    status: str
    severity: str
    title: str
    detail: str = ""
    evidence: str = ""
    suggestion: str = ""
    policy_code: str = ""
    policy_title: str = ""
    policy_clause: str = ""
    source: str = "rule"
    confidence: float = 1.0

    def as_dict(self) -> dict:
        return {
            "code": self.code, "name": self.name, "status": self.status,
            "severity": self.severity, "title": self.title, "detail": self.detail,
            "evidence": self.evidence, "suggestion": self.suggestion,
            "policy_code": self.policy_code, "policy_title": self.policy_title,
            "policy_clause": self.policy_clause, "source": self.source,
            "confidence": round(self.confidence, 3),
        }


def _policy(query: str) -> tuple[str, str, str]:
    hits = search_policies(query, limit=1)
    if not hits:
        return "", "", ""
    h = hits[0]
    return h["code"], h["title"], h["clause"]


def _parse_date(s: str):
    try:
        return date.fromisoformat(str(s)[:10])
    except Exception:
        return None


def _city_of(item: dict, claim: dict) -> str:
    return (item.get("city") or claim.get("destination") or "其他").strip()


def _hotel_limit(city: str) -> float:
    for name, limit in config.CITY_HOTEL_LIMIT.items():
        if name != "其他" and name in city:
            return float(limit)
    return float(config.CITY_HOTEL_LIMIT["其他"])


def _trip_days(claim: dict) -> int:
    start, end = _parse_date(claim.get("trip_start")), _parse_date(claim.get("trip_end"))
    if start and end and end >= start:
        return (end - start).days + 1
    return 1


def rule_missing_docs(claim, docs, items, ctx):
    out = []
    if not docs:
        out.append(CheckResult("R018", "票据完整性", "fail", "high", "未上传任何票据",
                               "报销单没有可校验的票据", "—", "请上传发票原件后重新提交",
                               *_policy("电子发票 原始PDF")))
    cats = {i["category"] for i in items}
    if claim.get("category") == "差旅费" and cats and not ({"住宿", "交通", "市内交通"} & cats):
        out.append(CheckResult("R017", "票据完整性", "fail", "medium",
                               "缺少交通或住宿票据", "未识别到有效交通或住宿票据",
                               f"已识别 {len(items)} 条明细", "请补充行程单或住宿发票",
                               *_policy("报销须提供交通票据 住宿发票")))
    return out


def rule_invoice_header(claim, docs, items, ctx):
    out = []
    for d in docs:
        f = d.get("fields") or {}
        buyer = str(f.get("buyer") or "")
        if not buyer:
            out.append(CheckResult("R001", "发票抬头", "warn", "medium",
                                   "未能识别购买方名称", "票据中未找到购买方信息",
                                   f"文件：{d['original_name']}", "请补充购买方信息或上传清晰原件",
                                   *_policy("发票购买方名称 纳税人识别号"),
                                   source="ocr" if d.get("method") == "ocr" else "rule",
                                   confidence=float(d.get("confidence") or 0.6)))
        elif config.COMPANY not in buyer:
            out.append(CheckResult("R001", "发票抬头", "fail", "high",
                                   "发票抬头与公司不一致", f"识别到购买方为“{buyer}”",
                                   f"文件：{d['original_name']}；购买方：{buyer}",
                                   "请退回申请人重新开具公司抬头发票",
                                   *_policy("发票抬头 公司名称 税号")))
    return out


def rule_duplicate_invoice(claim, docs, items, ctx):
    out = []
    conn = ctx["conn"]
    for d in docs:
        inv = (d.get("fields") or {}).get("invoice_no")
        if not inv:
            continue
        row = conn.execute(
            "SELECT c.claim_no FROM line_items li JOIN claims c ON c.id=li.claim_id"
            " WHERE li.invoice_no=? AND c.id<>? LIMIT 1", (inv, claim["id"])
        ).fetchone()
        if row:
            out.append(CheckResult("R002", "发票重复", "fail", "high",
                                   "发票号码已在其他报销单中使用",
                                   f"发票号 {inv} 已被 {row['claim_no']} 使用",
                                   f"发票号码：{inv}", "禁止重复报销，请核实",
                                   *_policy("发票号码不得重复使用")))
    return out


def rule_hotel_limit(claim, docs, items, ctx):
    out = []
    by_night = {}
    for item in items:
        if item["category"] != "住宿":
            continue
        city = _city_of(item, claim)
        limit = _hotel_limit(city)
        nights = max(int(item.get("nights") or 1), 1)
        per_night = float(item["amount"]) / nights
        by_night.setdefault((city, item.get("occurred_on")), []).append(item)
        if per_night > limit + 0.01:
            out.append(CheckResult("R003", "住宿标准", "warn", "medium",
                                   f"{city}住宿费超标",
                                   f"每晚 {per_night:.2f} 元，超出 {city} 限额 {limit:.0f} 元",
                                   f"票据：{item.get('invoice_no') or '—'}；{item['amount']:.2f} 元 / {nights} 晚",
                                   f"建议按 {limit:.0f} 元/晚报销，超支由个人承担",
                                   *_policy("住宿费 限额 城市 每晚")))
    for (city, day), group in by_night.items():
        if len(group) > 1:
            total = sum(float(g["amount"]) for g in group)
            out.append(CheckResult("R004", "住宿重复", "fail", "high",
                                   f"{day} 存在多张住宿发票",
                                   f"同城同日 {len(group)} 张住宿发票，合计 {total:.2f} 元",
                                   "；".join(f"{g.get('invoice_no') or g['merchant']} {g['amount']:.2f}" for g in group),
                                   "请确认是否重复报销",
                                   *_policy("同一晚不得重复报销住宿费")))
    return out


def rule_trip_window(claim, docs, items, ctx):
    out = []
    start, end = _parse_date(claim.get("trip_start")), _parse_date(claim.get("trip_end"))
    if not start or not end:
        return [CheckResult("R005", "行程完整性", "fail", "medium", "缺少出差起止日期",
                            "无法校验票据日期是否落在行程内",
                            f"行程：{claim.get('trip_start') or '—'} 至 {claim.get('trip_end') or '—'}",
                            "请补充出差申请单", *_policy("出差申请 起止日期 目的地"))]
    if end < start:
        return [CheckResult("R006", "行程完整性", "fail", "high", "行程日期倒置",
                            f"结束 {end} 早于开始 {start}", f"{start} → {end}", "请修正行程日期",
                            *_policy("出差 起止日期"))]
    for item in items:
        d = _parse_date(item.get("occurred_on"))
        if d and (d < start or d > end):
            out.append(CheckResult("R007", "票据日期", "fail", "medium",
                                   "票据日期不在出差期间",
                                   f"{item['category']}票据日期 {d} 不在 {start} 至 {end} 内",
                                   f"票据：{item.get('invoice_no') or '—'}；{item['amount']:.2f} 元",
                                   "请说明原因或补充行程审批",
                                   *_policy("票据日期应与行程一致 住宿日期")))
    return out


def rule_invoice_date(claim, docs, items, ctx):
    out = []
    submitted = _parse_date(claim.get("submitted_at")) or date.today()
    for d in docs:
        f = d.get("fields") or {}
        inv_date = _parse_date(f.get("invoice_date"))
        if not inv_date:
            continue
        if inv_date > submitted:
            out.append(CheckResult("R008", "开票日期", "fail", "high",
                                   "发票开票日期晚于提交日期",
                                   f"开票 {inv_date} 晚于提交 {submitted}",
                                   f"文件：{d['original_name']}", "请核实发票真实性",
                                   *_policy("开票日期不得晚于报销提交日期")))
        elif (submitted - inv_date).days > 30:
            out.append(CheckResult("R009", "报销时限", "warn", "medium",
                                   "超过30天报销时限",
                                   f"开票 {inv_date}，距提交 {(submitted - inv_date).days} 天",
                                   f"文件：{d['original_name']}", "请部门负责人说明逾期原因",
                                   *_policy("费用发生后30日内提交报销")))
    return out


def rule_amount_consistency(claim, docs, items, ctx):
    out = []
    invoice_total = sum(float((d.get("fields") or {}).get("total") or 0) for d in docs)
    item_total = sum(float(i["amount"]) for i in items)
    claimed = float(claim.get("claimed_amount") or 0)
    if invoice_total and abs(invoice_total - item_total) > 0.02:
        out.append(CheckResult("R010", "金额一致性", "fail", "high",
                               "票据合计与明细合计不一致",
                               f"票据 {invoice_total:.2f} 元，明细 {item_total:.2f} 元，差额 {item_total - invoice_total:+.2f}",
                               f"票据 {len(docs)} 张；明细 {len(items)} 条", "请核对明细或补充票据",
                               *_policy("发票金额与报销明细金额必须一致")))
    if abs(item_total - claimed) > 0.02:
        out.append(CheckResult("R011", "申报金额", "fail", "medium",
                               "申报金额与明细合计不一致",
                               f"申报 {claimed:.2f} 元，明细 {item_total:.2f} 元",
                               f"差额 {claimed - item_total:+.2f} 元", "请修正申报金额",
                               *_policy("报销金额 明细 一致")))
    return out


def rule_meal_and_transport(claim, docs, items, ctx):
    out = []
    days = _trip_days(claim)
    meal = sum(float(i["amount"]) for i in items if i["category"] == "餐饮")
    taxi = sum(float(i["amount"]) for i in items if i["category"] == "市内交通")
    if meal > config.MEAL_LIMIT_PER_DAY * days + 0.01:
        out.append(CheckResult("R012", "餐饮标准", "warn", "medium",
                               "餐饮费用超过包干标准",
                               f"餐饮 {meal:.2f} 元，{days} 天上限 {config.MEAL_LIMIT_PER_DAY * days} 元",
                               "按发票汇总", "伙食补助按天包干，超出部分不予报销",
                               *_policy("伙食补助费 每天100元 包干")))
    if taxi > config.LOCAL_TRANSPORT_LIMIT_PER_DAY * days + 0.01:
        out.append(CheckResult("R013", "市内交通", "warn", "medium",
                               "市内交通费超过包干标准",
                               f"交通 {taxi:.2f} 元，{days} 天上限 {config.LOCAL_TRANSPORT_LIMIT_PER_DAY * days} 元",
                               "按发票汇总", "市内交通按天包干，超出部分不予报销",
                               *_policy("市内交通费 每天80元 包干")))
    return out


def rule_entertainment(claim, docs, items, ctx):
    out = []
    ent = [i for i in items if i["category"] == "业务招待"]
    total = sum(float(i["amount"]) for i in ent)
    if total > config.ENTERTAIN_THRESHOLD:
        out.append(CheckResult("R014", "招待费审批", "warn", "medium",
                               "大额招待费缺少事前审批",
                               f"招待费 {total:.2f} 元，超过 {config.ENTERTAIN_THRESHOLD} 元",
                               "；".join(f"{i.get('merchant')} {i['amount']:.2f}" for i in ent),
                               "请补充招待申请单与审批记录",
                               *_policy("业务招待费 2000元 审批")))
    if ent and any(i["category"] in ("住宿", "交通") for i in items):
        out.append(CheckResult("R015", "费用混报", "warn", "low",
                               "招待费与差旅费混报", "同一报销单同时包含招待费与差旅费用",
                               f"招待 {total:.2f} 元", "建议拆分单据",
                               *_policy("招待费与差旅费分开填报")))
    return out


def rule_route_consistency(claim, docs, items, ctx):
    out = []
    dest = (claim.get("destination") or "").strip()
    if not dest:
        return out
    for d in docs:
        f = d.get("fields") or {}
        to_city = str(f.get("to_city") or "")
        if to_city and dest not in to_city and to_city not in dest:
            out.append(CheckResult("R016", "行程一致性", "warn", "medium",
                                   "票据目的地与出差地不一致",
                                   f"票据目的地 {to_city}，申请目的地 {dest}",
                                   f"文件：{d['original_name']}", "请说明变更原因",
                                   *_policy("交通票据 出发地 目的地 与出差申请一致")))
    return out


RULES = [
    rule_missing_docs, rule_invoice_header, rule_duplicate_invoice, rule_hotel_limit,
    rule_trip_window, rule_invoice_date, rule_amount_consistency,
    rule_meal_and_transport, rule_entertainment, rule_route_consistency,
]


PASS_TITLES = {
    "rule_missing_docs": ("票据完整性", "票据齐全，未发现缺件"),
    "rule_invoice_header": ("发票抬头", "购买方名称与公司一致"),
    "rule_duplicate_invoice": ("发票重复", "发票号码未在其他报销单中使用"),
    "rule_hotel_limit": ("住宿标准", "住宿单价未超过城市限额"),
    "rule_trip_window": ("票据日期", "票据日期均落在出差期间内"),
    "rule_invoice_date": ("开票日期", "开票日期与提交时间符合要求"),
    "rule_amount_consistency": ("金额一致性", "票据、明细与申报金额一致"),
    "rule_meal_and_transport": ("包干标准", "餐饮与市内交通未超包干上限"),
    "rule_entertainment": ("招待费", "招待费未触及审批阈值"),
    "rule_route_consistency": ("行程一致性", "票据目的地与出差地一致"),
}


def run_rules(claim, docs, items, ctx):
    results: list[CheckResult] = []
    for rule in RULES:
        try:
            found = rule(claim, docs, items, ctx)
            results.extend(found)
            if not found:
                name, title = PASS_TITLES.get(rule.__name__, (rule.__name__, "校验通过"))
                results.append(CheckResult(
                    "PASS", name, "pass", "low", title,
                    "该项校验未发现异常", "", "", "", "", "", source="rule", confidence=1.0,
                ))
        except Exception as exc:
            results.append(CheckResult("R999", "规则执行异常", "warn", "low",
                                       f"规则 {rule.__name__} 执行失败", str(exc)[:200],
                                       "", "请人工复核该规则", source="system", confidence=0.0))

    # LLM 语义规则（未配置模型时自动跳过，不产生占位项）
    try:
        from .semantic import rule_semantic_match
        results.extend(rule_semantic_match(claim, docs, items, ctx))
    except Exception as exc:
        results.append(CheckResult("R999", "语义规则异常", "warn", "low",
                                   "语义匹配规则执行失败", str(exc)[:160], "",
                                   "请人工复核", source="system", confidence=0.0))
    return results


def summarize(claim, checks, docs, items) -> dict:
    fails = [c for c in checks if c.status == "fail"]
    warns = [c for c in checks if c.status == "warn"]
    high = [c for c in fails if c.severity == "high"]
    medium = [c for c in fails if c.severity == "medium"]
    claimed = float(claim.get("claimed_amount") or 0)
    days = _trip_days(claim)

    approved = 0.0
    for item in items:
        cat = item["category"]
        amount = float(item["amount"])
        if cat == "住宿":
            nights = max(int(item.get("nights") or 1), 1)
            approved += min(amount, _hotel_limit(_city_of(item, claim)) * nights)
        elif cat in ("餐饮", "市内交通"):
            continue
        else:
            approved += amount
    approved += min(sum(float(i["amount"]) for i in items if i["category"] == "餐饮"),
                    config.MEAL_LIMIT_PER_DAY * days)
    approved += min(sum(float(i["amount"]) for i in items if i["category"] == "市内交通"),
                    config.LOCAL_TRANSPORT_LIMIT_PER_DAY * days)
    approved = round(min(approved, claimed) if claimed else approved, 2)

    if high:
        approved = 0.0

        decision, risk, confidence = "reject", "high", 0.96
        summary = f"发现 {len(high)} 项高风险问题，建议退回：" + "；".join(c.title for c in high[:3])
    elif medium or warns:
        decision, risk = "review", "medium"
        confidence = round(max(0.62, 0.9 - 0.06 * len(fails) - 0.03 * len(warns)), 3)
        summary = f"发现 {len(fails) + len(warns)} 项待确认问题，建议人工复核：" + "；".join(
            c.title for c in (fails + warns)[:3])
    else:
        decision, risk, confidence = "pass", "low", (0.95 if len(docs) >= 2 else 0.9)
        summary = f"票据与行程一致，未发现违规项，建议通过；可报金额 {approved:.2f} 元。"

    if decision == "pass" and confidence < config.AUTO_PASS_CONFIDENCE:
        decision = "review"
        summary += "（置信度未达自动通过阈值）"

    return {
        "decision": decision, "risk_level": risk, "confidence": round(confidence, 3),
        "approved_amount": approved, "claimed_amount": claimed, "summary": summary,
        "counts": {"pass": len([c for c in checks if c.status == "pass"]),
                   "warn": len(warns), "fail": len(fails)},
    }

