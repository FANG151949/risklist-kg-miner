"""v0.1 规则抽取：任免公告文本 -> 结构化记录 + 行号证据。"""

from __future__ import annotations

import re
from dataclasses import dataclass

APPOINTMENT_RE = re.compile(
    r"^(?P<action>任命|免去)\s*(?P<person>[\u4e00-\u9fff]{2,4})"
    r"\s*(?:为|的)\s*"
    r"(?P<org>.+?(?:委员会|局|公司|院|部|中心))"
    r"(?P<position>[^；;。\s]*?)"
    r"\s*(?:职务)?\s*[；;。]\s*$"
)

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


def extract_appointments(text: str, source: str = "") -> list[AppointmentRecord]:
    effective = _effective_date(text)
    records = []
    for no, line in enumerate(text.splitlines(), start=1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = APPOINTMENT_RE.match(s)
        if m:
            records.append(
                AppointmentRecord(
                    action=m.group("action"),
                    person=m.group("person"),
                    org=m.group("org"),
                    position=m.group("position"),
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
