"""规则抽取：任免公告文本 -> 结构化记录 + 行号证据。

v0.1 支持段落式（版式 A）；v0.5 增加表格版式（B）与叙述版式（C 及变体）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_ORG_SUFFIX = r"(?:委员会|局|公司|院|部|中心)"

APPOINTMENT_RE = re.compile(
    r"^(?P<action>任命|免去)\s*(?P<person>[\u4e00-\u9fff]{2,4})"
    r"\s*(?:为|的)\s*"
    r"(?P<org>.+?" + _ORG_SUFFIX + r")"
    r"(?P<position>[^；;。\s]*?)"
    r"\s*(?:职务)?\s*[；;。]\s*$"
)

TABLE_RE = re.compile(
    r"^\|?\s*(?P<person>[\u4e00-\u9fff]{2,4})\s*\|\s*(?P<action>任命|免去)\s*\|\s*"
    r"(?P<org>[^|]+?)\s*\|\s*(?P<position>[^|]+?)\s*$"
)

# 叙述式：句子内嵌任免表述，用 search 定位。命名的动作为固定值。
NARRATIVE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (
        re.compile(
            r"(?P<person>[\u4e00-\u9fff]{2,4})同志任"
            r"(?P<org>.+?" + _ORG_SUFFIX + r")"
            r"(?P<position>[^，。；;、\s]*)"
        ),
        "任命",
    ),
    (
        re.compile(
            r"(?:免去)?(?P<person>[\u4e00-\u9fff]{2,4})同志的"
            r"(?P<org>.+?" + _ORG_SUFFIX + r")"
            r"(?P<position>[^，。；;、\s]*?)职务"
        ),
        "免去",
    ),
    (
        re.compile(
            r"(?P<person>[\u4e00-\u9fff]{2,4})不再担任"
            r"(?P<org>.+?" + _ORG_SUFFIX + r")"
            r"(?P<position>[^，。；;、\s]*?)职务"
        ),
        "免去",
    ),
]

EFFECTIVE_RE = re.compile(
    r"自\s*(?P<y>\d{4})年(?P<m>\d{1,2})月(?P<d>\d{1,2})日\s*起生效"
)


@dataclass(frozen=True)
class AppointmentRecord:
    action: str
    person: str
    org: str
    position: str
    line_no: int
    raw: str
    effective_date: str | None = None
    source: str = ""


def _effective_date(text: str) -> str | None:
    m = EFFECTIVE_RE.search(text)
    if not m:
        return None
    return f"{m.group('y')}-{int(m.group('m')):02d}-{int(m.group('d')):02d}"


def _match_line(s: str) -> tuple[str, str, str, str] | None:
    """一条文本按 版式A->B->C 顺序尝试，命中即返回 (action, person, org, position)。"""
    m = APPOINTMENT_RE.match(s) or TABLE_RE.match(s)
    if m:
        d = m.groupdict()
        return d["action"], d["person"], d["org"], d["position"]
    for pattern, action in NARRATIVE_PATTERNS:
        m = pattern.search(s)
        if m:
            d = m.groupdict()
            return action, d["person"], d["org"], d["position"]
    return None


def extract_appointments(text: str, source: str = "") -> list[AppointmentRecord]:
    effective = _effective_date(text)
    records = []
    for no, line in enumerate(text.splitlines(), start=1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        hit = _match_line(s)
        if hit:
            action, person, org, position = hit
            if not person or not org or not position:
                continue  # 缺关键字段宁可漏掉也不产出脏记录
            records.append(
                AppointmentRecord(
                    action=action,
                    person=person,
                    org=org,
                    position=position,
                    line_no=no,
                    raw=s,
                    effective_date=effective,
                    source=source,
                )
            )
    return records


if __name__ == "__main__":
    import sys
    from pathlib import Path

    if len(sys.argv) != 2:
        raise SystemExit("用法: python -m risklist_kg_miner.extract <公告文本路径>")
    text = Path(sys.argv[1]).read_text(encoding="utf-8")
    records = extract_appointments(text)
    for r in records:
        eff = f" (生效 {r.effective_date})" if r.effective_date else ""
        print(f"L{r.line_no} {r.action} {r.person} -> {r.org} / {r.position}{eff}")
    print(f"共 {len(records)} 条任免记录")
