"""Neo4j 存储：执行 GraphOp 落库 + 多跳查询示例（建图 agent 的执行侧）。

连接参数取环境变量，缺省为本地开发实例（NEO4J_AUTH=neo4j/password）。
"""

from __future__ import annotations

import os

from .extract import AppointmentRecord
from .graph_model import GraphOp

DEFAULT_URI = "bolt://localhost:7687"
DEFAULT_USER = "neo4j"
DEFAULT_PASSWORD = "password"

PERSON_TIMELINE = """\
MATCH (p:Person {name: $person})-[:SUBJECT]->(e)-[:AT]->(o)
RETURN e.date AS date, e.action AS action, o.name AS org, e.position AS position
ORDER BY size(e.date) = 0, date
"""

ORG_POSITION_HISTORY = """\
MATCH (p:Person)-[:SUBJECT]->(e {position: $position})-[:AT]->(o:Org {name: $org})
RETURN p.name AS person, e.action AS action, e.date AS date
ORDER BY size(e.date) = 0, date
"""

CURRENT_STAFF = """\
MATCH (p:Person)-[r:STAFF]->(o:Org {name: $org})
RETURN p.name AS person, r.position AS position, r.status AS status, r.note AS note
"""

NOTICE_SOURCES = "MATCH (n:Notice) RETURN DISTINCT n.source AS source"

LOAD_RECORDS = """\
MATCH (p:Person)-[:SUBJECT]->(e:Event)<-[:RECORDS]-(n:Notice)
MATCH (e)-[:AT]->(o:Org)
RETURN n.source AS source, e.id AS event_id, e.action AS action,
       p.name AS person, o.name AS org, e.position AS position, e.date AS date
"""


class Neo4jStore:
    def __init__(
        self,
        uri: str | None = None,
        user: str | None = None,
        password: str | None = None,
    ):
        uri = uri or os.environ.get("NEO4J_URI", DEFAULT_URI)
        user = user or os.environ.get("NEO4J_USER", DEFAULT_USER)
        password = password or os.environ.get("NEO4J_PASSWORD", DEFAULT_PASSWORD)
        from neo4j import GraphDatabase  # 延迟导入：没装驱动只影响真连库

        self._driver = GraphDatabase.driver(uri, auth=(user, password))

    def apply(self, ops: list[GraphOp]) -> int:
        with self._driver.session() as session:
            for op in ops:
                session.run(op.statement, **op.params)
        return len(ops)

    def query(self, cypher: str, **params) -> list[dict]:
        with self._driver.session() as session:
            return [dict(row) for row in session.run(cypher, **params)]

    def notice_sources(self) -> list[str]:
        """增量更新用：图内已有哪些公告。"""
        return [row["source"] for row in self.query(NOTICE_SOURCES)]

    def load_records(self) -> list[AppointmentRecord]:
        """增量更新的历史事实源：把 :Event 流水读回成记录，参与本次合并。

        事件流水是不可变留痕，读回它就能让"只处理新公告"的增量跑法
        依然看见全部历史任免——跨公告冲突不因增量而漏检。
        """
        out: list[AppointmentRecord] = []
        for row in self.query(LOAD_RECORDS):
            eid = row["event_id"]
            line_no = int(eid.rsplit("#L", 1)[-1]) if "#L" in eid else 0
            out.append(
                AppointmentRecord(
                    action=row["action"],
                    person=row["person"],
                    org=row["org"],
                    position=row["position"],
                    line_no=line_no,
                    raw="",
                    effective_date=row["date"] or None,
                    source=row["source"],
                )
            )
        return out

    def close(self) -> None:
        self._driver.close()


def _demo() -> None:
    from pathlib import Path

    from .collect import html_to_text
    from .extract import extract_appointments
    from .graph_model import build_ops
    from .validate import merge

    samples = Path(__file__).resolve().parent.parent.parent / "data" / "samples"
    records = extract_appointments(
        (samples / "notice-001.txt").read_text(encoding="utf-8"), source="notice-001"
    )
    for name in ("notice-002", "notice-003", "notice-004", "notice-005", "notice-006"):
        html = (samples / f"{name}.html").read_text(encoding="utf-8")
        records += extract_appointments(html_to_text(html), source=name)
    ops = build_ops(records, merge(records))

    store = Neo4jStore()
    try:
        store.apply(ops)
        print(f"落库 {len(ops)} 条操作")

        print("\n== 陈志远任免时间线（事件流水，冲突双方留痕） ==")
        for row in store.query(PERSON_TIMELINE, person="陈志远"):
            print(f"  {row['date'] or '日期待核'} {row['action']} {row['org']}{row['position']}")

        print("\n== 河州市发改委副主任历史（多跳：人->事件->机构） ==")
        for row in store.query(
            ORG_POSITION_HISTORY,
            org="河州市发展和改革委员会",
            position="副主任",
        ):
            print(f"  {row['person']} {row['action']}（{row['date'] or '日期待核'}）")

        print("\n== 河州市发改委当前任职状态（合并结论，conflict 即待人工复核） ==")
        for row in store.query(CURRENT_STAFF, org="河州市发展和改革委员会"):
            print(f"  {row['person']} {row['position']} [{row['status']}] {row['note']}")
    finally:
        store.close()


if __name__ == "__main__":
    _demo()
