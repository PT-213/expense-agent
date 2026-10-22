"""报销政策库：种子数据 + 条款级检索（供证据绑定使用）。"""
from __future__ import annotations

import re

import jieba

from ..db import db, now_iso

POLICIES = [
    {
        "code": "FIN-TRV-01",
        "title": "差旅费管理办法",
        "category": "差旅",
        "effective_date": "2025-01-01",
        "content": (
            "第一条 住宿费实行限额管理。北京、上海每人每晚不超过700元；深圳650元；广州600元；"
            "杭州550元；南京500元；成都480元；武汉、厦门450元；其他城市380元。超出部分由个人承担。\n"
            "第二条 伙食补助费按出差自然天数计算，每人每天100元，包干使用。\n"
            "第三条 市内交通费每人每天80元，包干使用，不再凭票报销。\n"
            "第四条 城市间交通费标准：部门经理及以上可乘坐高铁一等座、飞机经济舱；其他人员乘坐高铁二等座、飞机经济舱。\n"
            "第五条 出差人员应在返回后十个工作日内提交报销，逾期需部门负责人说明原因。\n"
            "第六条 报销须提供出差申请单、交通票据、住宿发票及费用明细，票据日期应与行程一致。"
        ),
    },
    {
        "code": "FIN-TRV-02",
        "title": "差旅住宿与城市标准补充规定",
        "category": "差旅",
        "effective_date": "2025-03-01",
        "content": (
            "第一条 住宿发票的入住与离店日期应落在出差审批的起止日期内。\n"
            "第二条 同一晚不得重复报销住宿费；两人同住一间时按实际房费报销，不得按人头拆分。\n"
            "第三条 住宿发票抬头必须为公司全称，税号必须与公司税号一致，否则不予报销。\n"
            "第四条 民宿、公寓类住宿需提供平台订单与付款记录，按同城限额的80%报销。"
        ),
    },
    {
        "code": "FIN-INV-01",
        "title": "发票合规与查验规定",
        "category": "票据",
        "effective_date": "2025-01-01",
        "content": (
            "第一条 报销发票必须真实、合法、有效，发票号码在系统内不得重复使用。\n"
            "第二条 发票购买方名称必须为“厦门商集网络科技有限责任公司”，纳税人识别号必须为 91350200MA2YQ8XK3B。\n"
            "第三条 发票开票日期不得晚于报销提交日期，且一般不得超过行程结束后30天。\n"
            "第四条 电子发票须上传原始PDF或OFD文件，截图、照片不予受理。\n"
            "第五条 发票金额与报销明细金额必须一致，价税合计不得小于明细合计。"
        ),
    },
    {
        "code": "FIN-ENT-01",
        "title": "业务招待费管理规定",
        "category": "招待",
        "effective_date": "2025-01-01",
        "content": (
            "第一条 业务招待费单次金额超过2000元的，须提前提交招待申请并经分管领导审批。\n"
            "第二条 招待费报销须注明招待对象单位、人数、事由，并附消费明细。\n"
            "第三条 不得报销高档烟酒、娱乐场所消费及与业务无关的支出。\n"
            "第四条 招待费与差旅费应分开填报，不得混在同一张报销单中。"
        ),
    },
    {
        "code": "FIN-SUB-01",
        "title": "报销时限与审批权限",
        "category": "流程",
        "effective_date": "2025-01-01",
        "content": (
            "第一条 费用发生后应在30个自然日内提交报销，跨年度费用不得报销。\n"
            "第二条 单笔报销金额5000元以下由部门负责人审批；5000元至20000元由财务负责人审批；20000元以上由总经理审批。\n"
            "第三条 报销单提交后不得直接修改，需退回申请人重新提交。\n"
            "第四条 同一事项不得拆分为多张报销单规避审批权限。"
        ),
    },
    {
        "code": "FIN-TRV-03",
        "title": "出差审批与行程一致性要求",
        "category": "差旅",
        "effective_date": "2025-06-01",
        "content": (
            "第一条 出差前须提交出差申请，注明目的地、起止日期与事由，经部门负责人批准。\n"
            "第二条 交通票据的出发地、目的地应与出差申请一致；因故变更的须补充说明。\n"
            "第三条 周末或法定节假日因私停留产生的住宿费不予报销。\n"
            "第四条 出差期间发生的餐饮、洗衣、迷你吧等个人消费不予报销。"
        ),
    },
]

STOP = set("的 了 和 与 及 在 是 为 对 将 等 中 上 下 也 有 我 你 他 我们 这 那 什么 哪些 怎么 如何 是否 可以 需要 应该".split())


def seed_policies() -> None:
    with db() as conn:
        if conn.execute("SELECT COUNT(*) c FROM policies").fetchone()["c"]:
            return
        for p in POLICIES:
            conn.execute(
                "INSERT INTO policies(code, title, category, content, effective_date, version, active, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (p["code"], p["title"], p["category"], p["content"], p["effective_date"], "v1", 1, now_iso()),
            )


def _terms(q: str) -> list[str]:
    words = [w for w in jieba.cut_for_search(q) if len(w) >= 2 and w not in STOP]
    words = list(dict.fromkeys(words))
    words.sort(key=len, reverse=True)
    kept: list[str] = []
    for w in words:
        if any(w != k and w in k for k in kept):
            continue
        kept.append(w)
    return kept[:6]


def search_policies(query: str, limit: int = 3) -> list[dict]:
    terms = _terms(query)
    if not terms:
        return []
    with db() as conn:
        rows = conn.execute("SELECT * FROM policies WHERE active=1").fetchall()
    scored = []
    for row in rows:
        text = f"{row['title']}\n{row['content']}"
        hit = sum(1 for t in terms if t in text)
        if not hit:
            continue
        clauses = [c.strip() for c in re.split(r"\n|(?=第[一二三四五六七八九十]+条)", row["content"]) if c.strip()]
        best = max(clauses, key=lambda c: sum(1 for t in terms if t in c)) if clauses else row["content"][:160]
        score = hit / len(terms) + min(len([t for t in terms if t in best]) / len(terms), 1) * 0.5
        scored.append({
            "policy_id": row["id"], "code": row["code"], "title": row["title"],
            "clause": best[:260], "score": round(score, 3), "effective_date": row["effective_date"],
        })
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]
