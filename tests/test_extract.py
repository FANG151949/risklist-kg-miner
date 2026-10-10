from pathlib import Path

from risklist_kg_miner.extract import extract_appointments

SAMPLE = Path(__file__).parent.parent / "data" / "samples" / "notice-001.txt"


def test_extract_sample_notice():
    text = SAMPLE.read_text(encoding="utf-8")
    records = extract_appointments(text)

    assert len(records) == 5
    first = records[0]
    assert first.action == "任命"
    assert first.person == "陈志远"
    assert first.org == "河州市发展和改革委员会"
    assert first.position == "副主任"
    assert first.line_no == 11
    assert first.effective_date == "2026-09-01"


def test_extract_line_numbers_track_original_text():
    text = SAMPLE.read_text(encoding="utf-8")
    records = extract_appointments(text)

    assert [r.line_no for r in records] == [11, 12, 13, 14, 15]


def test_short_org_name_kept_as_is():
    # 孙丽华一行的机构写作"市卫生健康委员会"（简写，缺少"河州"前缀），
    # v0.1 原样保留，归一化是后续版本（校验/图谱层）的痛点。
    text = SAMPLE.read_text(encoding="utf-8")
    records = extract_appointments(text)

    sun = records[4]
    assert sun.person == "孙丽华"
    assert sun.org == "市卫生健康委员会"
    assert sun.position == "副调研员"


def test_no_match_returns_empty_list():
    assert extract_appointments("这是一段没有任免内容的普通文字。") == []
