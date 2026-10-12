from pathlib import Path

from risklist_kg_miner.collect import html_to_text
from risklist_kg_miner.extract import extract_appointments
from risklist_kg_miner.scorer import review_queue, score
from risklist_kg_miner.validate import MergeResult, MergeStatus, merge
from risklist_kg_miner.extract import AppointmentRecord

SAMPLES = Path(__file__).parent.parent / "data" / "samples"


def _result(action, person, org, position, status, sources, date=None):
    record = AppointmentRecord(
        action=action, person=person, org=org, position=position,
        line_no=1, raw="", effective_date=date, source=sources[0],
    )
    return MergeResult(record, status, "测试", tuple(sources))


def test_conflict_is_high_risk_and_queued():
    m = _result("免去", "陈志远", "河州市发展和改革委员会", "副主任",
                MergeStatus.CONFLICT, ["notice-001", "notice-006"])
    a = score([m])[0]
    assert a.score == 40 + 25 + 10  # conflict + 免去 + 无生效日期
    assert a.level == "高"
    assert a.recommendation == "转人工复核"
    assert review_queue(score([m])) == [a]


def test_conflict_overrides_score_for_queue():
    # conflict 即使分数被拉低也必须进队列——状态未定就不能下结论。
    m = _result("任命", "某人", "某市某局", "科员",
                MergeStatus.CONFLICT, ["a", "b"], date="2026-01-01")
    a = score([m])[0]
    assert a.score == 40
    assert len(review_queue([a])) == 1


def test_removal_single_source_no_date_is_high():
    m = _result("免去", "吴国强", "市公路管理局", "局长",
                MergeStatus.CONFIRMED, ["notice-004"], date=None)
    a = score([m])[0]
    assert a.score == 25 + 10 + 10
    assert a.level == "高"
    assert a.recommendation == "纳入重点名单"
    assert review_queue([a]) == []  # 高风险但状态明确，不占人工复核资源


def test_multi_source_appointment_is_low():
    m = _result("任命", "周晓梅", "河州市交通运输局", "副局长",
                MergeStatus.MERGED, ["notice-001", "notice-004"], date="2026-09-01")
    a = score([m])[0]
    assert a.score == 0
    assert a.level == "低"
    assert a.recommendation == "常规"


def test_real_samples_only_chen_conflicted_goes_to_queue():
    records = extract_appointments(
        (SAMPLES / "notice-001.txt").read_text(encoding="utf-8"), source="notice-001"
    )
    for name in ("notice-002", "notice-003", "notice-004", "notice-005", "notice-006"):
        records += extract_appointments(
            html_to_text((SAMPLES / f"{name}.html").read_text(encoding="utf-8")),
            source=name,
        )
    assessments = score(merge(records))
    queue = review_queue(assessments)

    assert len(assessments) == 11  # 14 条记录按 (人, 职务) 合并成 11 组
    assert len(queue) == 1
    assert queue[0].person == "陈志远"
    # 高风险不只是冲突：免职+单源+无日期也能到 45 分
    wu = [a for a in assessments if a.person == "吴国强"][0]
    assert wu.level == "高"
    sun = [a for a in assessments if a.person == "孙丽华"][0]
    assert sun.level == "中" and sun.status == MergeStatus.MERGED
