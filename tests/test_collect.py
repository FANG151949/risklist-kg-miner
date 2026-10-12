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


def test_table_layout_extracts_records():
    # 版式 B 表格行 "| 姓名 | 任免 | 机构 | 职务"（采集器给单元格加前导分隔符）。
    # v0.5 起支持。
    records = extract_appointments(_collect("notice-003.html"))
    assert len(records) == 2
    assert records[0].person == "孙丽华"
    assert records[0].action == "免去"
    assert records[0].org == "河州市卫生健康委员会"
    assert records[0].position == "副调研员"
    assert records[0].effective_date == "2026-09-10"
    assert records[1].person == "郑海涛"
    assert records[1].action == "任命"
    assert records[1].org == "河州市疾病预防控制中心"


def test_narrative_layout_extracts_records():
    # 版式 C 叙述句："同志任……" / "免去……同志的……职务"。v0.5 起支持。
    records = extract_appointments(_collect("notice-004.html"))
    assert len(records) == 2
    assert records[0].person == "周晓梅"
    assert records[0].action == "任命"
    assert records[0].org == "河州市交通运输局"
    assert records[0].position == "副局长"
    assert records[1].person == "吴国强"
    assert records[1].action == "免去"
    assert records[1].org == "市公路管理局"
    assert records[1].position == "局长"

def test_variant_narrative_layout_extracts_records():
    # 用户自造的叙述变体："不再担任……职务"。
    records = extract_appointments(_collect("notice-005.html"))
    assert len(records) == 1
    assert records[0].person == "冯建华"
    assert records[0].action == "免去"
    assert records[0].org == "市水利局"
    assert records[0].position == "局长"
