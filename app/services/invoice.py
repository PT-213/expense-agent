"""票据解析：文本层优先，扫描件走 OCR；抽取发票号、日期、金额、购销方与城市。"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pymupdf

from .. import config

_ocr_engine = None


def ocr_engine():
    global _ocr_engine
    if _ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _ocr_engine = RapidOCR()
    return _ocr_engine


@dataclass
class ParsedDoc:
    kind: str
    text: str
    method: str
    confidence: float
    image_path: str = ""
    fields: dict = field(default_factory=dict)


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _clean(text: str) -> str:
    text = text.replace("\u3000", " ")
    text = text.replace("０", "0").replace("１", "1").replace("２", "2")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _num(s: str) -> float:
    s = s.replace(",", "").replace("¥", "").replace("￥", "").strip()
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group(0)) if m else 0.0


def _date(s: str) -> str:
    m = re.search(r"((?:19|20)\d{2})\s*[-年/.]\s*(\d{1,2})\s*[-月/.]\s*(\d{1,2})", s)
    if not m:
        return ""
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def detect_kind(text: str, filename: str) -> str:
    hay = f"{filename}\n{text}"
    if "航空运输电子客票行程单" in hay or ("行程单" in hay and "航班" in hay):
        return "air"
    if "铁路电子客票" in hay or "中国铁路" in hay or re.search(r"\bG\d{2,4}\b", hay):
        return "train"
    if "住宿服务" in hay or "住宿费" in hay:
        return "hotel"
    # 招待优先于餐饮：同一张餐饮发票可能标注为业务招待
    if "业务招待" in hay or "招待费" in hay or "娱乐服务" in hay:
        return "entertainment"
    if "餐饮服务" in hay or "餐费" in hay:
        return "meal"
    if "运输服务" in hay or "客运服务" in hay or "出租" in hay:
        return "taxi"
    return "invoice"


CITY_RE = re.compile(r"(北京|上海|广州|深圳|杭州|南京|成都|武汉|厦门|苏州|西安|重庆|天津|青岛|长沙|郑州|宁波|合肥|福州|济南)")


def parse_fields(text: str, kind: str) -> dict:
    fields: dict = {"kind": kind}
    compact = re.sub(r"[ \t]+", " ", text)

    m = re.search(r"发票号码[:：]?\s*([0-9]{8,20})", compact)
    if m:
        fields["invoice_no"] = m.group(1)
    else:
        m = re.search(r"(?:行程单号|票据号码|票号)[:：]?\s*([0-9A-Z]{6,20})", compact)
        if m:
            fields["invoice_no"] = m.group(1)

    m = re.search(r"发票代码[:：]?\s*([0-9]{10,12})", compact)
    if m:
        fields["invoice_code"] = m.group(1)

    m = re.search(r"开票日期[:：]?\s*([^\n]+)", compact) or re.search(r"日期[:：]?\s*([^\n]+)", compact)
    if m:
        fields["invoice_date"] = _date(m.group(1))
    if not fields.get("invoice_date"):
        fields["invoice_date"] = _date(compact[:600])

    # 发票版式：购买方 / 名称：... 分两行；也兼容 购买方名称：... 单行
    lines = [l.strip() for l in compact.splitlines() if l.strip()]
    for i, line in enumerate(lines):
        if line.startswith("购买方"):
            inline = re.split(r"[:：]", line, maxsplit=1)
            if len(inline) == 2 and inline[1].strip():
                fields["buyer"] = inline[1].strip()
                break
            for nxt in lines[i + 1:i + 3]:
                m2 = re.match(r"名称[:：]\s*(.+)", nxt)
                if m2:
                    fields["buyer"] = m2.group(1).strip()
                    break
            break
    for i, line in enumerate(lines):
        if line.startswith("销售方"):
            inline = re.split(r"[:：]", line, maxsplit=1)
            if len(inline) == 2 and inline[1].strip():
                fields["seller"] = inline[1].strip()
                break
            for nxt in lines[i + 1:i + 3]:
                m2 = re.match(r"名称[:：]\s*(.+)", nxt)
                if m2:
                    fields["seller"] = m2.group(1).strip()
                    break
            break
    if not fields.get("seller"):
        m = re.search(r"销售方(?:名称)?[:：]\s*([^\n]+)", compact)
        if m:
            fields["seller"] = m.group(1).strip()

    m = re.search(r"价税合计[（(]?小写[)）]?[:：]?\s*[¥￥]?\s*([\d,]+\.\d{2})", compact)
    if not m:
        m = re.search(r"价税合计[^\n]*?[¥￥]\s*([\d,]+\.\d{2})", compact)
    if not m:
        m = re.search(r"合\s*计[^\n]*?[¥￥]?\s*([\d,]+\.\d{2})", compact)
    if m:
        fields["total"] = _num(m.group(1))

    m = re.search(r"税\s*额[^\n]*?[¥￥]?\s*([\d,]+\.\d{2})", compact)
    if m:
        fields["tax"] = _num(m.group(1))
    m = re.search(r"金\s*额[^\n]*?[¥￥]?\s*([\d,]+\.\d{2})", compact)
    if m:
        fields["amount"] = _num(m.group(1))

    if kind == "air":
        m = re.search(r"合\s*计[^\n]*?([\d,]+\.\d{2})", compact)
        if m:
            fields["total"] = _num(m.group(1))
        m = re.search(r"航班号[:：]?\s*([A-Z0-9]{3,8})", compact)
        if m:
            fields["flight"] = m.group(1)
        m = re.search(r"自\s*([^\s→]+)\s*(?:至|到|→)\s*([^\s\n]+)", compact)
        if m:
            fields["from_city"], fields["to_city"] = m.group(1), m.group(2)
    if kind == "train":
        m = re.search(r"(?:票价|票价合计)[:：]?\s*[¥￥]?\s*([\d,]+\.\d{2})", compact)
        if m:
            fields["total"] = _num(m.group(1))
        m = re.search(r"([^\s]{2,6})\s*[→\-—]\s*([^\s]{2,6})", compact)
        if m:
            fields["from_city"], fields["to_city"] = m.group(1), m.group(2)
        m = re.search(r"车次[:：]?\s*([GDCZTK]\d{1,4})", compact) or re.search(r"\b([GDCZTK]\d{1,4})\b", compact)
        if m:
            fields["train_no"] = m.group(1)

    if kind == "hotel":
        m = re.search(r"入住日期[:：]?\s*([^\n]+)", compact)
        if m:
            fields["check_in"] = _date(m.group(1))
        m = re.search(r"离店日期[:：]?\s*([^\n]+)", compact)
        if m:
            fields["check_out"] = _date(m.group(1))
        m = re.search(r"共\s*(\d+)\s*晚", compact)
        if m:
            fields["nights"] = int(m.group(1))

    cities = CITY_RE.findall(f"{fields.get('seller','')}\n{compact[:400]}")
    if cities:
        fields["city"] = cities[-1]
    elif fields.get("to_city") in config.CITY_HOTEL_LIMIT:
        fields["city"] = fields["to_city"]

    items = re.findall(r"\*([^*\n]{2,12})\*([^\n]{0,30})", compact)
    if items:
        fields["items"] = [f"{a} {b}".strip() for a, b in items[:6]]
    return fields


def parse_pdf(path: Path, original_name: str) -> ParsedDoc:
    doc = pymupdf.open(path)
    texts, method, conf = [], "text", 1.0
    image_path = ""
    try:
        for page in doc:
            texts.append(_clean(page.get_text("text")))
        joined = "\n".join(texts)
        if len(re.sub(r"\s", "", joined)) < 30:
            method = "ocr"
            pix = doc[0].get_pixmap(dpi=config.OCR_DPI, colorspace=pymupdf.csRGB)
            image_path = str(config.PAGE_DIR / f"{path.stem}.png")
            pix.save(image_path)
            result, _ = ocr_engine()(image_path)
            lines = []
            for item in result or []:
                try:
                    score = float(item[2])
                except (TypeError, ValueError):
                    score = 0.0
                lines.append((item[1], score))
            joined = _clean("\n".join(t for t, _ in lines))
            conf = round(sum(s for _, s in lines) / len(lines), 4) if lines else 0.0
    finally:
        doc.close()

    kind = detect_kind(joined, original_name)
    fields = parse_fields(joined, kind)
    fields["confidence"] = conf
    return ParsedDoc(kind=kind, text=joined, method=method, confidence=conf,
                     image_path=image_path, fields=fields)
