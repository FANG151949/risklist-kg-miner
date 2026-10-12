from pathlib import Path

from risklist_kg_miner.extract import AppointmentRecord
from risklist_kg_miner.pipeline import run

SAMPLES = Path(__file__).parent.parent / "data" / "samples"


def rec(action, person, org, position, line_no, source):
    return AppointmentRecord(
        action=action, person=person, org=org, position=position,
        line_no=line_no, raw="", source=source,
    )


class FakeStore:
    """最小假图库：只实现流水线用到的三个接口。"""

    def __init__(self, sources=(), records=()):
        self.sources = list(sources)
        self.records = list(records)
        self.applied = []
        self.load_calls = 0

    def notice_sources(self):
        return self.sources

    def load_records(self):
        self.load_calls += 1
        return self.records

    def apply(self, ops):
        self.applied += ops
        return len(ops)


CHEN_APPOINT = rec("任命", "陈志远", "河州市发展和改革委员会", "副主任", 11, "notice-001")

NOTICE_007 = (
    "<html><body><p>免去陈志远同志的河州市发展和改革委员会副主任职务。</p>"
    "</body></html>"
)


def test_full_run_over_samples():
    r = run(store=FakeStore())
    assert r.processed == [
        "notice-001", "notice-002", "notice-003",
        "notice-004", "notice-005", "notice-006",
    ]
    assert r.skipped == [] and r.failures == []
    assert r.records == 14
    assert r.groups == 11
    assert r.conflicts == 1
    assert r.graph_applied is True
    assert r.queue == ["陈志远@河州市发展和改革委员会副主任"]


def test_incremental_skips_known_sources():
    known = ["notice-001", "notice-002", "notice-003",
             "notice-004", "notice-005", "notice-006"]
    store = FakeStore(sources=known)
    r = run(store=store)
    assert r.skipped == known
    assert r.processed == []
    assert r.records == 0 and r.groups == 0
    assert r.graph_applied is False
    assert store.load_calls == 0  # 没有新公告时连历史都不读


def test_incremental_merges_new_notice_against_history(tmp_path):
    # 图内已有 notice-001（陈志远任命）；目录里只有新公告 notice-007（陈志远免职）。
    # 增量只抽取 notice-007 一篇，但合并必须看见历史 -> 跨公告冲突不漏检。
    (tmp_path / "notice-007.html").write_text(NOTICE_007, encoding="utf-8")
    store = FakeStore(sources=["notice-001"], records=[CHEN_APPOINT])
    r = run(samples_dir=tmp_path, store=store)

    assert r.processed == ["notice-007"]
    assert r.records == 1
    assert r.groups == 1
    assert r.conflicts == 1  # 历史(任命) + 新(免去) -> 保守结论转人工复核
    assert r.queue == ["陈志远@河州市发展和改革委员会副主任"]
    assert any(
        op.params.get("event_id", "").startswith("notice-007") for op in store.applied
    )


def test_force_reruns_without_history(tmp_path):
    # --force 全量重跑：不掺图内历史，只合并本次抽取的记录。
    (tmp_path / "notice-007.html").write_text(NOTICE_007, encoding="utf-8")
    store = FakeStore(sources=["notice-007"], records=[CHEN_APPOINT])
    r = run(samples_dir=tmp_path, store=store, force=True)

    assert r.processed == ["notice-007"]
    assert r.skipped == []
    assert store.load_calls == 0
    assert r.groups == 1
    assert r.conflicts == 0  # 单源免职记录 -> confirmed，无历史任命可冲突


def test_failure_isolation_continues_batch(tmp_path):
    # 一篇坏文件（非法 UTF-8）只记名跳过，不毁掉整批处理。
    (tmp_path / "notice-007.html").write_text(NOTICE_007, encoding="utf-8")
    (tmp_path / "broken.html").write_bytes(b"\xff\xfe\x00 not utf8")
    r = run(samples_dir=tmp_path, store=None)

    assert len(r.failures) == 1
    assert r.failures[0].startswith("broken.html")
    assert r.processed == ["notice-007"]
    assert r.records == 1
    assert r.groups == 1
