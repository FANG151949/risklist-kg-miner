from pathlib import Path

from risklist_kg_miner.collect import html_to_text
from risklist_kg_miner.extract import extract_appointments

SAMPLES = Path(__file__).parent.parent / "data" / "samples"


def _collect(name: str) -> str:
    return html_to_text((SAMPLES / name).read_text(encoding="utf-8"))


def test_boilerplate_removed():
    text = _collect("notice-002.html")
    assert "备案号" not in text  # footer 被剔除
    assert "互动交流" not in text  # nav 被剔除
    assert "_hmt" not in text  # script 被剔除
    assert "江州市人民政府任免通知" in text  # 正文保留


def test_paragraph_layout_extracts_records():
    records = extract_appointments(_collect("notice-002.html"))
    assert len(records) == 2
    assert records[0].person == "罗建军"
    assert records[0].org == "江州市商务局"
    assert records[0].effective_date == "2026-08-20"


def test_table_layout_yields_nothing_yet():
    # 版式 B 的行结构是 "姓名 | 任免 | 机构 | 职务"，与 APPOINTMENT_RE 的
    # 句式完全不同 -> 0 条。这是 v0.3 要解决的痛点，此处先钉死现状。
    records = extract_appointments(_collect("notice-003.html"))
    assert records == []


def test_narrative_layout_yields_nothing_yet():
    # 版式 C 是叙述句 "经研究，某某同志任……"，同样 0 条。
    records = extract_appointments(_collect("notice-004.html"))
    assert records == []
