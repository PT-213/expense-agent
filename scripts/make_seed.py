"""生成演示报销单与票据 PDF。"""
from __future__ import annotations

import io
import json
import random
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFilter
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "seed" / "invoices"
OUT.mkdir(parents=True, exist_ok=True)

FONT_DIR = Path(r"C:\Windows\Fonts")
pdfmetrics.registerFont(TTFont("Song", str(FONT_DIR / "simsun.ttc")))
pdfmetrics.registerFont(TTFont("Hei", str(FONT_DIR / "simhei.ttf")))

COMPANY = "厦门商集网络科技有限责任公司"
TAX_ID = "91350200MA2YQ8XK3B"


def invoice_pdf(path, invoice_no, invoice_date, seller, seller_tax, item, spec, qty,
                unit_price, amount, tax_rate, tax, total, buyer=COMPANY, buyer_tax=TAX_ID,
                extra=None, title="厦门增值税电子普通发票", invoice_code="035002100311"):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    y = h - 60
    c.setFont("Hei", 18)
    c.drawCentredString(w / 2, y, title)
    y -= 34
    c.setLineWidth(0.8)
    c.setStrokeColorRGB(0.45, 0.45, 0.45)
    c.line(50, y, w - 50, y)
    y -= 22
    c.setFont("Song", 10.5)
    c.drawString(54, y, f"发票代码：{invoice_code}")
    c.drawRightString(w - 54, y, f"发票号码：{invoice_no}")
    y -= 18
    c.drawString(54, y, f"开票日期：{invoice_date}")
    c.drawRightString(w - 54, y, "校验码：8271 3390 4412 8836")
    y -= 24
    c.setFont("Hei", 10.5)
    c.drawString(54, y, "购买方")
    c.setFont("Song", 10.5)
    c.drawString(110, y, f"名称：{buyer}")
    y -= 17
    c.drawString(110, y, f"纳税人识别号：{buyer_tax}")
    y -= 22
    c.setFont("Hei", 10.5)
    c.drawString(54, y, "销售方")
    c.setFont("Song", 10.5)
    c.drawString(110, y, f"名称：{seller}")
    y -= 17
    c.drawString(110, y, f"纳税人识别号：{seller_tax}")
    y -= 26
    cols = [54, 150, 250, 300, 350, 410, 470, 520]
    headers = ["项目名称", "规格型号", "单位", "数量", "单价", "金额", "税率", "税额"]
    c.setFont("Hei", 9.5)
    for x, text in zip(cols, headers):
        c.drawString(x, y, text)
    y -= 9
    c.line(50, y, w - 50, y)
    y -= 20
    c.setFont("Song", 9.5)
    for x, text in zip(cols, [item, spec, "次", qty, unit_price, amount, tax_rate, tax]):
        c.drawString(x, y, text)
    y -= 18
    c.line(50, y, w - 50, y)
    y -= 22
    c.setFont("Song", 10.5)
    c.drawString(54, y, "合计")
    c.drawRightString(430, y, f"¥{amount}")
    c.drawRightString(w - 54, y, f"¥{tax}")
    y -= 24
    c.setFont("Hei", 11)
    c.drawString(54, y, f"价税合计（小写）：¥{total}")
    y -= 18
    c.setFont("Song", 10.5)
    c.drawString(54, y, "（大写）人民币捌佰元整")
    y -= 26
    if extra:
        c.setFont("Song", 10)
        for line in extra:
            c.drawString(54, y, line)
            y -= 16
    y = max(y, 90)
    c.setFont("Song", 9.5)
    c.drawString(54, y, "收款人：林小敏")
    c.drawString(200, y, "复核：陈芳")
    c.drawString(340, y, "开票人：王强")
    c.setFont("Song", 8)
    c.setFillColorRGB(0.5, 0.5, 0.5)
    c.drawString(54, 46, "本发票为演示数据，仅用于系统验证，不具有任何法律效力")
    c.save()
    path.write_bytes(buf.getvalue())


def train_ticket(path, no, date, from_city, to_city, train_no, price, seat="二等座"):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    y = h - 70
    c.setFont("Hei", 18)
    c.drawCentredString(w / 2, y, "中国铁路电子客票")
    y -= 40
    c.setFont("Song", 11)
    c.drawString(60, y, f"票号：{no}")
    c.drawRightString(w - 60, y, f"开车日期：{date}")
    y -= 30
    c.setFont("Hei", 14)
    c.drawString(60, y, from_city)
    c.drawString(220, y, "→")
    c.drawString(260, y, to_city)
    c.setFont("Song", 12)
    c.drawRightString(w - 60, y, f"车次：{train_no}")
    y -= 28
    c.drawString(60, y, f"座位：{seat}  08车12F号")
    c.drawRightString(w - 60, y, f"票价：¥{price}")
    y -= 28
    c.drawString(60, y, "乘车人：陈伟")
    c.drawRightString(w - 60, y, "身份证号：3502**********1234")
    y -= 36
    c.setFont("Song", 10)
    c.drawString(60, y, f"购买方名称：{COMPANY}")
    y -= 18
    c.drawString(60, y, f"纳税人识别号：{TAX_ID}")
    y -= 30
    c.setFont("Song", 8)
    c.setFillColorRGB(0.5, 0.5, 0.5)
    c.drawString(60, 46, "演示票据，仅用于系统验证")
    c.save()
    path.write_bytes(buf.getvalue())


def to_scan(pdf_path):
    src = pymupdf.open(pdf_path)
    out = io.BytesIO()
    images = []
    for page in src:
        pix = page.get_pixmap(dpi=200, colorspace=pymupdf.csRGB)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        img = img.rotate(random.uniform(-0.4, 0.4), resample=Image.BICUBIC, fillcolor=(252, 251, 247))
        overlay = Image.new("RGB", img.size, (250, 249, 245))
        img = Image.blend(img, overlay, 0.05)
        draw = ImageDraw.Draw(img)
        for _ in range(220):
            x, y = random.randint(0, img.width - 1), random.randint(0, img.height - 1)
            draw.point((x, y), fill=(random.randint(190, 235),) * 3)
        images.append(img.filter(ImageFilter.GaussianBlur(0.3)))
    src.close()
    c = canvas.Canvas(out, pagesize=A4)
    for img in images:
        tmp = io.BytesIO()
        img.save(tmp, "JPEG", quality=88)
        tmp.seek(0)
        c.drawImage(ImageReader(tmp), 0, 0, width=A4[0], height=A4[1])
        c.showPage()
    c.save()
    pdf_path.write_bytes(out.getvalue())


def main():
    random.seed(20261019)
    for old in OUT.glob("*.pdf"):
        old.unlink()
    claims = []

    invoice_pdf(OUT / "INV-1001-hotel.pdf", "24567891", "2026年10月09日",
                "厦门海丝酒店管理有限公司", "91350203MA31H6R2XK", "*住宿服务*住宿费", "标准间",
                "2", "400.00", "800.00", "6%", "48.00", "800.00",
                extra=["入住日期：2026年10月08日", "离店日期：2026年10月10日", "住宿天数：共 2 晚"])
    train_ticket(OUT / "INV-1002-train.pdf", "E183472901", "2026年10月08日", "厦门", "上海", "G1652", "280.00")
    invoice_pdf(OUT / "INV-1003-meal.pdf", "24567892", "2026年10月09日",
                "厦门悦食餐饮有限公司", "91350203MA2YQ1111A", "*餐饮服务*餐费", "工作餐",
                "1", "260.00", "260.00", "6%", "15.60", "260.00")
    invoice_pdf(OUT / "INV-1004-taxi.pdf", "24567893", "2026年10月09日",
                "厦门海韵出租汽车有限公司", "91350203MA2YQ2222B", "*运输服务*客运服务", "出租车",
                "1", "120.00", "120.00", "9%", "9.91", "120.00")
    claims.append({"applicant": "chenwei", "purpose": "上海客户拜访与方案沟通",
                   "trip_start": "2026-10-08", "trip_end": "2026-10-10", "destination": "上海",
                   "category": "差旅费", "claimed_amount": 1460.00,
                   "documents": ["INV-1001-hotel.pdf", "INV-1002-train.pdf", "INV-1003-meal.pdf", "INV-1004-taxi.pdf"],
                   "submitted_at": "2026-10-12T09:30:00+08:00"})

    invoice_pdf(OUT / "INV-2001-hotel.pdf", "24610001", "2026年10月06日",
                "上海浦江国际酒店有限公司", "91310115MA1K3X9L2P", "*住宿服务*住宿费", "商务大床房",
                "2", "980.00", "1960.00", "6%", "117.60", "1960.00",
                extra=["入住日期：2026年10月05日", "离店日期：2026年10月07日", "住宿天数：共 2 晚"])
    train_ticket(OUT / "INV-2002-train.pdf", "E183473002", "2026年10月05日", "厦门", "上海", "G7321", "320.00")
    claims.append({"applicant": "chenwei", "purpose": "上海行业展会参会",
                   "trip_start": "2026-10-05", "trip_end": "2026-10-07", "destination": "上海",
                   "category": "差旅费", "claimed_amount": 2280.00,
                   "documents": ["INV-2001-hotel.pdf", "INV-2002-train.pdf"],
                   "submitted_at": "2026-10-09T10:10:00+08:00"})

    invoice_pdf(OUT / "INV-3001-hotel-dup.pdf", "24567891", "2026年10月09日",
                "厦门海丝酒店管理有限公司", "91350203MA31H6R2XK", "*住宿服务*住宿费", "标准间",
                "2", "400.00", "800.00", "6%", "48.00", "800.00",
                extra=["入住日期：2026年10月08日", "离店日期：2026年10月10日", "住宿天数：共 2 晚"])
    claims.append({"applicant": "zhangmin", "purpose": "厦门供应商现场审核",
                   "trip_start": "2026-10-08", "trip_end": "2026-10-10", "destination": "厦门",
                   "category": "差旅费", "claimed_amount": 800.00,
                   "documents": ["INV-3001-hotel-dup.pdf"],
                   "submitted_at": "2026-10-13T14:20:00+08:00"})

    invoice_pdf(OUT / "INV-4001-hotel-wrongbuyer.pdf", "24620001", "2026年10月11日",
                "杭州西湖云栖酒店有限公司", "91330106MA2B7Y8K1Q", "*住宿服务*住宿费", "标准间",
                "1", "520.00", "520.00", "6%", "31.20", "520.00",
                buyer="厦门商集贸易有限公司",
                extra=["入住日期：2026年10月10日", "离店日期：2026年10月11日", "住宿天数：共 1 晚"])
    claims.append({"applicant": "liuyang", "purpose": "杭州渠道伙伴洽谈",
                   "trip_start": "2026-10-10", "trip_end": "2026-10-11", "destination": "杭州",
                   "category": "差旅费", "claimed_amount": 520.00,
                   "documents": ["INV-4001-hotel-wrongbuyer.pdf"],
                   "submitted_at": "2026-10-12T16:40:00+08:00"})

    invoice_pdf(OUT / "INV-5001-meal.pdf", "24630001", "2026年10月14日",
                "成都锦里餐饮管理有限公司", "91510104MA6C9K2L3R", "*餐饮服务*餐费", "工作餐",
                "1", "180.00", "180.00", "6%", "10.80", "180.00")
    claims.append({"applicant": "zhangmin", "purpose": "成都合作方拜访",
                   "trip_start": "", "trip_end": "", "destination": "成都",
                   "category": "差旅费", "claimed_amount": 180.00,
                   "documents": ["INV-5001-meal.pdf"],
                   "submitted_at": "2026-10-15T11:00:00+08:00"})

    invoice_pdf(OUT / "INV-6001-ent.pdf", "24640001", "2026年10月16日",
                "厦门鹭江宴餐饮有限公司", "91350203MA2YQ6666C", "*餐饮服务*业务招待", "商务宴请",
                "1", "3200.00", "3200.00", "6%", "192.00", "3200.00",
                extra=["招待对象：上海某某科技有限公司 5 人", "事由：年度合作洽谈"])
    claims.append({"applicant": "chenwei", "purpose": "年度合作洽谈招待",
                   "trip_start": "2026-10-16", "trip_end": "2026-10-16", "destination": "厦门",
                   "category": "业务招待费", "claimed_amount": 3200.00,
                   "documents": ["INV-6001-ent.pdf"],
                   "submitted_at": "2026-10-16T20:30:00+08:00"})

    invoice_pdf(OUT / "INV-7001-taxi.pdf", "24650001", "2026年10月25日",
                "南京金陵出租汽车有限公司", "91320104MA1YQ7777D", "*运输服务*客运服务", "出租车",
                "1", "95.00", "95.00", "9%", "7.84", "95.00")
    claims.append({"applicant": "liuyang", "purpose": "南京客户现场支持",
                   "trip_start": "2026-10-18", "trip_end": "2026-10-20", "destination": "南京",
                   "category": "差旅费", "claimed_amount": 95.00,
                   "documents": ["INV-7001-taxi.pdf"],
                   "submitted_at": "2026-10-20T09:00:00+08:00"})

    invoice_pdf(OUT / "INV-8001-hotel-scan.pdf", "24660001", "2026年10月12日",
                "武汉江城酒店有限公司", "91420106MA4K8L9M2N", "*住宿服务*住宿费", "标准间",
                "2", "430.00", "860.00", "6%", "51.60", "860.00",
                extra=["入住日期：2026年10月11日", "离店日期：2026年10月13日", "住宿天数：共 2 晚"])
    to_scan(OUT / "INV-8001-hotel-scan.pdf")
    claims.append({"applicant": "zhangmin", "purpose": "武汉客户交付支持",
                   "trip_start": "2026-10-11", "trip_end": "2026-10-13", "destination": "武汉",
                   "category": "差旅费", "claimed_amount": 860.00,
                   "documents": ["INV-8001-hotel-scan.pdf"],
                   "submitted_at": "2026-10-14T10:00:00+08:00"})

    (ROOT / "seed" / "demo_claims.json").write_text(
        json.dumps({"claims": claims}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"generated {len(list(OUT.glob('*.pdf')))} invoices, {len(claims)} claims")


if __name__ == "__main__":
    main()
