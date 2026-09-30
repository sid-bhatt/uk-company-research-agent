"""Build small, valid iXBRL accounts documents for tests.

Real filed accounts contain directors' names in their approval statements, so tests use
synthetic documents with the same structure as FRS 102 micro-entity filings.
"""

import datetime as dt
from dataclasses import dataclass, field
from itertools import count

DURATION_CONCEPTS = {"TurnoverRevenue", "ProfitLoss", "AverageNumberEmployeesDuringPeriod"}


@dataclass
class Tag:
    concept: str
    period_end: str
    text: str  # as displayed, e.g. "12,345" or "-"
    attrs: dict[str, str] = field(default_factory=dict)  # sign, scale, format, id…
    dimension: tuple[str, str] | None = None  # (dimension, member) local names


def make_ixbrl(tags: list[Tag]) -> bytes:
    contexts: dict[tuple[str, bool, tuple[str, str] | None], str] = {}
    ids = count(1)
    body = []
    for t in tags:
        key = (t.period_end, t.concept in DURATION_CONCEPTS, t.dimension)
        cid = contexts.setdefault(key, f"c{next(ids)}")
        attrs = {"name": f"core:{t.concept}", "contextRef": cid, "unitRef": "GBP", "decimals": "0"}
        attrs.update(t.attrs)
        rendered = " ".join(f'{k}="{v}"' for k, v in attrs.items())
        tag = f"<ix:nonFraction {rendered}>{t.text}</ix:nonFraction>"
        body.append(f"<tr><td>{t.concept}</td><td>{tag}</td></tr>")

    ctx_xml = []
    for (end, duration, dim), cid in contexts.items():
        end_d = dt.date.fromisoformat(end)
        if duration:
            start = end_d.replace(year=end_d.year - 1) + dt.timedelta(days=1)
            period = (
                f"<xbrli:startDate>{start}</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate>"
            )
        else:
            period = f"<xbrli:instant>{end}</xbrli:instant>"
        segment = (
            f'<xbrli:segment><xbrldi:explicitMember dimension="core:{dim[0]}">core:{dim[1]}'
            "</xbrldi:explicitMember></xbrli:segment>"
            if dim
            else ""
        )
        ctx_xml.append(
            f'<xbrli:context id="{cid}"><xbrli:entity><xbrli:identifier scheme="http://www.companieshouse.gov.uk/">'
            f"00000000</xbrli:identifier>{segment}</xbrli:entity><xbrli:period>{period}</xbrli:period></xbrli:context>"
        )

    return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
      xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2011-07-31"
      xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
      xmlns:core="http://xbrl.frc.org.uk/fr/2025-01-01/core" xmlns:iso4217="http://www.xbrl.org/2003/iso4217">
<head><title>Synthetic accounts</title></head>
<body>
<div style="display:none"><ix:header><ix:resources>
{"".join(ctx_xml)}
<xbrli:unit id="GBP"><xbrli:measure>iso4217:GBP</xbrli:measure></xbrli:unit>
</ix:resources></ix:header></div>
<table>{"".join(body)}</table>
</body></html>""".encode()


def micro_accounts(
    year_end: str, prior_end: str, now: dict[str, int], before: dict[str, int]
) -> bytes:
    """A micro-entity balance sheet with current and prior-year columns."""
    tags = []
    for end, figures in ((year_end, now), (prior_end, before)):
        for concept, value in figures.items():
            attrs = {"sign": "-"} if value < 0 else {}
            tags.append(Tag(concept, end, f"{abs(value):,}", attrs))
    return make_ixbrl(tags)
