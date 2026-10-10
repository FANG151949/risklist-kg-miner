"""v0.4 图谱建模：记录与合并结论 -> Cypher 操作（纯函数，可离线测试）。

建模决策（事件流水 + 当前状态双写）：
- 每条原始抽取记录 -> 一个 Event 节点：不可变的事实流水，含来源与行号，
  冲突双方的任免事件都留痕，审计时可回放。
- 每个合并结论 -> 一条 (Person)-[:STAFF]->(Org) 关系：当前任职状态，
  status 保留 confirmed/merged/conflict，conflict 也入图转人工复核。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .extract import AppointmentRecord
from .validate import MergeResult

UPSERT_EVENT = """\
MERGE (p:Person {name: $person})
MERGE (o:Org {name: $org})
MERGE (n:Notice {source: $source})
MERGE (e:Event {id: $event_id})
SET e.action = $action, e.position = $position, e.date = $date
MERGE (p)-[:SUBJECT]->(e)
MERGE (e)-[:AT]->(o)
MERGE (n)-[:RECORDS]->(e)
"""

UPSERT_STAFF = """\
MERGE (p:Person {name: $person})
MERGE (o:Org {name: $org})
MERGE (p)-[r:STAFF]->(o)
SET r.position = $position, r.status = $status, r.note = $note, r.sources = $sources
"""


@dataclass(frozen=True)
class GraphOp:
    statement: str
    params: dict[str, Any]


def record_to_op(r: AppointmentRecord) -> GraphOp:
    source = r.source or f"inline-L{r.line_no}"
    return GraphOp(
        UPSERT_EVENT,
        {
            "person": r.person,
            "org": r.org,
            "source": source,
            "event_id": f"{source}#L{r.line_no}",
            "action": r.action,
            "position": r.position,
            "date": r.effective_date or "",
        },
    )


def result_to_op(m: MergeResult) -> GraphOp:
    r = m.record
    return GraphOp(
        UPSERT_STAFF,
        {
            "person": r.person,
            "org": r.org,
            "position": r.position,
            "status": m.status,
            "note": m.note,
            "sources": list(m.sources),
        },
    )


def build_ops(
    records: list[AppointmentRecord], results: list[MergeResult]
) -> list[GraphOp]:
    return [record_to_op(r) for r in records] + [result_to_op(m) for m in results]


if __name__ == "__main__":
    from pathlib import Path

    from .collect import html_to_text
    from .extract import extract_appointments
    from .validate import merge

    samples = Path(__file__).resolve().parent.parent.parent / "data" / "samples"
    records = extract_appointments(
        (samples / "notice-001.txt").read_text(encoding="utf-8"), source="notice-001"
    )
    for name in ("notice-002", "notice-003", "notice-004", "notice-005", "notice-006"):
        html = (samples / f"{name}.html").read_text(encoding="utf-8")
        records += extract_appointments(html_to_text(html), source=name)

    ops = build_ops(records, merge(records))
    events = [op for op in ops if op.statement is UPSERT_EVENT]
    staff = [op for op in ops if op.statement is UPSERT_STAFF]
    print(f"事件流水 ops: {len(events)} 条（来自 {len(records)} 条原始记录）")
    print(f"任职状态 ops: {len(staff)} 条（来自合并结论）")
    chen = [op.params for op in staff if op.params["person"] == "陈志远"]
    print(f"陈志远当前状态: {chen[0]['status']} / {chen[0]['position']} @ {chen[0]['org']}")
    print("\nNeo4j 启动命令（见 docs/tutorial-04-graph.md 步骤 1），"
          "然后用 uv run python -m risklist_kg_miner.neo4j_store 落库。")
