from pathlib import Path

from risklist_kg_miner.collect import html_to_text
from risklist_kg_miner.extract import AppointmentRecord, extract_appointments
from risklist_kg_miner.validate import (
    FakeLLM,
    MergeStatus,
    _org_matches,
    merge,
)

SAMPLES = Path(__file__).parent.parent / "data" / "samples"


def rec(action, person, org, position, line_no, source):
    return AppointmentRecord(
        action=action,
        person=person,
        org=org,
        position=position,
        line_no=line_no,
        raw="",
        source=source,
    )


SUN_ALIAS = rec("任命", "孙丽华", "市卫生健康委员会", "副调研员", 15, "notice-001")
SUN_FULL = rec("任命", "孙丽华", "河州市卫生健康委员会", "副调研员", 6, "notice-003")

CHEN_APPOINT = rec("任命", "陈志远", "河州市发展和改革委员会", "副主任", 11, "notice-001")
CHEN_REMOVE = rec("免去", "陈志远", "河州市发展和改革委员会", "副主任", 7, "notice-006")


def test_single_record_is_confirmed():
    results = merge([CHEN_APPOINT])
    assert len(results) == 1
    assert results[0].status == MergeStatus.CONFIRMED
    assert results[0].sources == ("notice-001",)


def test_org_alias_merges_to_full_name():
    # 孙丽华在 notice-001 是简称、notice-003 是全称，规则层直接合并取全称，
    # 不需要动用 LLM。
    results = merge([SUN_ALIAS, SUN_FULL])
    assert len(results) == 1
    assert results[0].status == MergeStatus.MERGED
    assert results[0].record.org == "河州市卫生健康委员会"
    assert results[0].sources == ("notice-001", "notice-003")


def test_conflict_without_llm_goes_to_review():
    # 陈志远：先任命后免去，没有 LLM 时取保守结论（免去优先）并转人工复核。
    results = merge([CHEN_APPOINT, CHEN_REMOVE])
    assert len(results) == 1
    assert results[0].status == MergeStatus.CONFLICT
    assert results[0].record.action == "免去"
    assert "人工复核" in results[0].note


def test_llm_adjudicates_conflict():
    # LLM 判定以候选 2（notice-006 免去）为准 -> 合并结论。
    llm = FakeLLM('{"latest": 2, "reason": "notice-006 日期更新"}')
    results = merge([CHEN_APPOINT, CHEN_REMOVE], llm=llm)
    assert results[0].status == MergeStatus.MERGED
    assert results[0].record.action == "免去"
    assert results[0].record.source == "notice-006"
    assert "notice-006" in results[0].note
    # prompt 里必须带上候选人名与来源，否则 LLM 是在瞎猜
    assert "陈志远" in llm.prompts[0]
    assert "notice-006" in llm.prompts[0]


def test_llm_failure_falls_back_to_conservative():
    # LLM 返回非法 JSON（网络抖动/模型抽风），解析失败 -> 保守降级，不炸。
    llm = FakeLLM("这不是 JSON")
    results = merge([CHEN_APPOINT, CHEN_REMOVE], llm=llm)
    assert results[0].status == MergeStatus.CONFLICT
    assert results[0].record.action == "免去"


def test_org_matches_suffix_rule():
    assert _org_matches("河州市卫生健康委员会", "市卫生健康委员会")
    assert _org_matches("市卫生健康委员会", "河州市卫生健康委员会")
    assert not _org_matches("河州市发展和改革委员会", "市卫生健康委员会")


def test_real_samples_chen_conflict_goes_to_review():
    # 陈志远在 notice-001 被任命、notice-006 被免去；无 LLM 时冲突不猜，
    # 取保守结论（免去优先）并转人工复核。
    r1 = extract_appointments(
        (SAMPLES / "notice-001.txt").read_text(encoding="utf-8"), source="notice-001"
    )
    r6 = extract_appointments(
        html_to_text((SAMPLES / "notice-006.html").read_text(encoding="utf-8")),
        source="notice-006",
    )
    chen = [r for r in r1 + r6 if r.person == "陈志远"]

    results = merge(chen)

    assert len(results) == 1
    assert results[0].status == MergeStatus.CONFLICT
    assert results[0].record.action == "免去"
    assert results[0].sources == ("notice-001", "notice-006")


def test_real_samples_alias_orgs_merge():
    # v0.5 版式适配后，孙丽华在 notice-001（简称机构）与 notice-003（全称）
    # 各有一条免去记录：动作一致 + 机构是简称/全称 -> 规则层合并取全称。
    r1 = extract_appointments(
        (SAMPLES / "notice-001.txt").read_text(encoding="utf-8"), source="notice-001"
    )
    r3 = extract_appointments(
        html_to_text((SAMPLES / "notice-003.html").read_text(encoding="utf-8")),
        source="notice-003",
    )
    sun = [r for r in r1 + r3 if r.person == "孙丽华"]

    results = merge(sun)

    assert len(results) == 1
    assert results[0].status == MergeStatus.MERGED
    assert results[0].record.org == "河州市卫生健康委员会"
    assert set(results[0].sources) == {"notice-001", "notice-003"}
