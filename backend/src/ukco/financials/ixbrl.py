"""Parse inline XBRL (iXBRL) accounts into numeric facts.

UK companies filing electronically (almost all small ones, ADR-004) submit accounts as
XHTML with machine-readable tags: `<ix:nonFraction name="core:NetAssetsLiabilities"
contextRef="c2" unitRef="GBP" decimals="0" sign="-" scale="0">1,234</ix:nonFraction>`.
Each fact's `contextRef` names a period (an instant for balance sheet items, a range
for P&L) and optional dimensions (e.g. "within one year").

Deterministic, stdlib-only. Every fact keeps a locator so a report can cite exactly
which tag in which document a figure came from.
"""

import datetime as dt
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

IX_NAMESPACES = ("http://www.xbrl.org/2013/inlineXBRL", "http://www.xbrl.org/2008/inlineXBRL")
XBRLI = "http://www.xbrl.org/2003/instance"
XBRLDI = "http://xbrl.org/2006/xbrldi"


class IxbrlError(ValueError):
    pass


@dataclass(frozen=True)
class Context:
    id: str
    start: dt.date | None  # None for instants
    end: dt.date  # instant date, or end of a duration
    dimensions: tuple[tuple[str, str], ...] = ()  # (dimension, member) local names

    @property
    def is_instant(self) -> bool:
        return self.start is None


@dataclass(frozen=True)
class Fact:
    concept: str  # local name, e.g. "NetAssetsLiabilities"
    prefix: str  # e.g. "core"
    value: Decimal
    unit: str | None
    context: Context
    locator: str  # how to find the tag again: element id, or name/context/occurrence
    decimals: str | None = None


@dataclass
class IxbrlDocument:
    facts: list[Fact] = field(default_factory=list)
    contexts: dict[str, Context] = field(default_factory=dict)

    def find(self, concept: str, *, dimensionless: bool = True) -> list[Fact]:
        return [
            f
            for f in self.facts
            if f.concept == concept and (not dimensionless or not f.context.dimensions)
        ]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _date(text: str | None) -> dt.date:
    if not text:
        raise IxbrlError("context period without a date")
    return dt.date.fromisoformat(text.strip()[:10])


def _parse_context(el: ET.Element) -> Context:
    cid = el.get("id") or ""
    period = el.find(f"{{{XBRLI}}}period")
    if period is None:
        raise IxbrlError(f"context {cid} has no period")
    instant = period.find(f"{{{XBRLI}}}instant")
    if instant is not None:
        start, end = None, _date(instant.text)
    else:
        start = _date(period.findtext(f"{{{XBRLI}}}startDate"))
        end = _date(period.findtext(f"{{{XBRLI}}}endDate"))
    dims = tuple(
        sorted(
            (_local_qname(m.get("dimension", "")), _local_qname((m.text or "").strip()))
            for m in el.iter(f"{{{XBRLDI}}}explicitMember")
        )
    )
    return Context(cid, start, end, dims)


def _local_qname(qname: str) -> str:
    return qname.rsplit(":", 1)[-1]


_NUMBER = re.compile(r"[^0-9.,]")


def _parse_number(el: ET.Element) -> Decimal:
    """Displayed text → value, honouring the ixt formats seen in UK filings."""
    text = "".join(el.itertext()).strip()
    fmt = _local_qname(el.get("format", ""))
    dashes = ("-", "\u2013", "\u2014", "")  # hyphen, en dash, em dash: all mean zero
    if fmt in ("fixed-zero", "zerodash", "fixed-empty") or text in dashes:
        return Decimal(0)
    digits = _NUMBER.sub("", text)
    if fmt in ("numcommadecimal", "num-comma-decimal", "numdotcomma"):  # 1.234,56
        digits = digits.replace(".", "").replace(",", ".")
    else:  # numdotdecimal / num-dot-decimal (the UK norm): 1,234.56
        digits = digits.replace(",", "")
    try:
        value = Decimal(digits or "0")
    except InvalidOperation as exc:
        raise IxbrlError(f"unparseable number {text!r}") from exc
    scale = int(el.get("scale", "0") or 0)
    value = value.scaleb(scale)
    return -value if el.get("sign") == "-" else value


def parse_ixbrl(content: bytes) -> IxbrlDocument:
    """Numeric facts with their contexts. Raises IxbrlError on non-iXBRL input."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise IxbrlError(f"not well-formed XHTML: {exc}") from exc

    doc = IxbrlDocument()
    for el in root.iter(f"{{{XBRLI}}}context"):
        ctx = _parse_context(el)
        doc.contexts[ctx.id] = ctx
    if not doc.contexts:
        raise IxbrlError("no XBRL contexts: not an iXBRL document")

    occurrences: dict[tuple[str, str], int] = {}
    for ns in IX_NAMESPACES:
        for el in root.iter(f"{{{ns}}}nonFraction"):
            name, ctx_ref = el.get("name", ""), el.get("contextRef", "")
            if ":" not in name or ctx_ref not in doc.contexts:
                continue
            prefix, concept = name.split(":", 1)
            n = occurrences[(name, ctx_ref)] = occurrences.get((name, ctx_ref), 0) + 1
            locator = (
                f"#{el.get('id')}"
                if el.get("id")
                else f"ix:nonFraction[name={name}][contextRef={ctx_ref}][{n}]"
            )
            doc.facts.append(
                Fact(
                    concept=concept,
                    prefix=prefix,
                    value=_parse_number(el),
                    unit=el.get("unitRef"),
                    context=doc.contexts[ctx_ref],
                    locator=locator,
                    decimals=el.get("decimals"),
                )
            )
    return doc
