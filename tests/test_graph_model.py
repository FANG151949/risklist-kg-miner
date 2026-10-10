from pathlib import Path

from risklist_kg_miner.collect import html_to_text
from risklist_kg_miner.extract import AppointmentRecord, extract_appointments
from risklist_kg_miner.graph_model import UPSERT_EVENT, UPSERT_STAFF, build_ops
from risklist_kg_miner.validate import MergeStatus, merge

SAMPLES = Path(__file__).parent.parent / "data" / "samples"

REC = AppointmentRecord(
    action="任命",
    person="陈志远",
    org="河州市发展和改革委员会",
    position="副主任",
    line_no=11,
    raw="",
    effective_date="2026-09-01",
    source="notice-001",
)


def test_record_to_op_captures_event_fields():
    op = build_ops([REC], [])[0]
    assert op.statement is UPSERT_EVENT
    assert op.params["event_id"] == "notice-001#L11"
    assert op.params["action"] == "任命"
    assert op.params["date"] == "2026-09-01"


def test_event_id_differs_across_sources():
    other = AppointmentRecord(
        action="免去",
        person="陈志远",
        org="河州市发展和改革委员会",
        position="副主任",
        line_no=7,
        raw="",
        source="notice-006",
    )
    ops = build_ops([REC, other], [])
    assert ops[0].params["event_id"] != ops[1].params["event_id"]


def test_ops_are_idempotent_merges_only():
    # 重跑整批操作不能产生重复数据：语句里只允许 MERGE/SET，禁止 CREATE。
    r1 = extract_appointments(
        (SAMPLES / "notice-001.txt").read_text(encoding="utf-8"), source="notice-001"
    )
    ops = build_ops(r1, merge(r1))
    assert ops and all("CREATE" not in op.statement for op in ops)


def test_real_samples_end_to_end_counts():
    # 真实样本端到端：6+1 条原始记录 -> 7 个事件 op；
    # 合并成 6 条结论（陈志远两条并一）-> 6 个任职状态 op。
    r1 = extract_appointments(
        (SAMPLES / "notice-001.txt").read_text(encoding="utf-8"), source="notice-001"
    )
    r6 = extract_appointments(
        html_to_text((SAMPLES / "notice-006.html").read_text(encoding="utf-8")),
        source="notice-006",
    )
    records = r1 + r6
    results = merge(records)

    ops = build_ops(records, results)
    events = [op for op in ops if op.statement is UPSERT_EVENT]
    staff = [op for op in ops if op.statement is UPSERT_STAFF]

    assert len(events) == 7
    assert len(staff) == 6
    chen = [op for op in staff if op.params["person"] == "陈志远"]
    assert len(chen) == 1
    assert chen[0].params["status"] == MergeStatus.CONFLICT


def test_empty_input_yields_empty_ops():
    assert build_ops([], []) == []
