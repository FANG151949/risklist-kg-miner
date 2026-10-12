"""v0.8 流水线封装：采集→抽取→合并→建图→评分 一键串联。

v0.7 之前是五个散装模块要按教程顺序手跑；真实使用是"每天来几篇新公告"：
- 增量：图内已有的公告跳过抽取；历史任免从 :Event 流水读回参与本次合并，
  跨公告冲突不因增量而漏检——图谱在这里是历史事实源，不只是查询加速器。
- 失败隔离：单篇公告解析失败记名继续跑，不毁整批。
- 软降级：Neo4j / LLM 任一不可用都不中断，按规则路跑完全程。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .collect import html_to_text
from .extract import AppointmentRecord, extract_appointments
from .graph_model import build_ops
from .scorer import RiskAssessment, review_queue, score
from .validate import MergeStatus, merge

SAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "samples"


@dataclass
class PipelineReport:
    processed: list[str] = field(default_factory=list)  # 本次抽取的公告
    skipped: list[str] = field(default_factory=list)  # 增量跳过（图内已有）
    failures: list[str] = field(default_factory=list)  # 失败隔离：坏文件记名继续
    records: int = 0
    groups: int = 0
    conflicts: int = 0
    graph_applied: bool = False
    degraded: list[str] = field(default_factory=list)
    assessments: list[RiskAssessment] = field(default_factory=list)
    queue: list[str] = field(default_factory=list)  # 人工复核队列 person@机构职务


def _extract_one(path: Path) -> list[AppointmentRecord]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".html":
        text = html_to_text(text)
    return extract_appointments(text, source=path.stem)


def run(
    samples_dir: Path | None = None,
    store=None,
    llm=None,
    force: bool = False,
) -> PipelineReport:
    samples_dir = Path(samples_dir or SAMPLES_DIR)
    report = PipelineReport()

    files = sorted(p for p in samples_dir.iterdir() if p.suffix in {".txt", ".html"})
    known: set[str] = set()
    if store is not None and not force:
        try:
            known = set(store.notice_sources())
        except Exception as e:
            report.degraded.append(f"graph:增量检查失败（{type(e).__name__}），转全量")

    records: list[AppointmentRecord] = []
    for path in files:
        if path.stem in known and not force:
            report.skipped.append(path.stem)
            continue
        try:
            records += _extract_one(path)
            report.processed.append(path.stem)
        except Exception as e:
            report.failures.append(f"{path.name}: {type(e).__name__}: {e}")

    history: list[AppointmentRecord] = []
    if store is not None and not force and report.processed:
        try:
            history = store.load_records()
        except Exception as e:
            report.degraded.append(
                f"graph:历史读回失败（{type(e).__name__}），合并仅覆盖本次公告"
            )

    results = merge(records + history, llm=llm) if (records or history) else []
    report.records = len(records)
    report.groups = len(results)
    report.conflicts = sum(1 for m in results if m.status == MergeStatus.CONFLICT)

    if store is not None and records:
        try:
            store.apply(build_ops(records + history, results))
            report.graph_applied = True
        except Exception as e:
            report.degraded.append(f"graph:落库失败（{type(e).__name__}），跳过图谱步骤")

    report.assessments = score(results)
    report.queue = [
        f"{a.person}@{a.org}{a.position}" for a in review_queue(report.assessments)
    ]
    return report
