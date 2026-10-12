"""v0.6 风险分级 scorer + 人工复核队列。

评分卡思路（风控域经典做法）：每个风险信号一个分值和理由，可解释、可单测、
零 LLM 成本。conflict 状态无论分数一律强制进人工复核队列——"宁可漏报不误报"
在名单产出环节的落地。
"""

from __future__ import annotations

from dataclasses import dataclass

from .validate import MergeResult, MergeStatus

# 信号 -> (分值, 理由模板)。分值来源是业务约定，调整需同步更新测试。
SIGNALS = {
    "conflict": (40, "任免状态跨公告冲突，未定"),
    "removed": (25, "存在免职记录（离任关注信号）"),
    "no_date": (10, "生效日期缺失，信息质量低"),
    "single_source": (10, "单源记录，未经交叉验证"),
}

LEVELS = ((40, "高"), (20, "中"), (0, "低"))


@dataclass(frozen=True)
class RiskAssessment:
    person: str
    org: str
    position: str
    status: str
    score: int
    level: str
    signals: tuple[tuple[str, int], ...]
    recommendation: str
    sources: tuple[str, ...]


def _level(score: int) -> str:
    for threshold, name in LEVELS:
        if score >= threshold:
            return name
    return "低"


def _recommendation(status: str, level: str) -> str:
    if status == MergeStatus.CONFLICT:
        return "转人工复核"
    if level == "高":
        return "纳入重点名单"
    if level == "中":
        return "持续观察"
    return "常规"


def score(results: list[MergeResult]) -> list[RiskAssessment]:
    assessments = []
    for m in results:
        r = m.record
        hits: list[tuple[str, int]] = []
        if m.status == MergeStatus.CONFLICT:
            hits.append(("conflict", SIGNALS["conflict"][0]))
        if r.action == "免去":
            hits.append(("removed", SIGNALS["removed"][0]))
        if not r.effective_date:
            hits.append(("no_date", SIGNALS["no_date"][0]))
        if len(m.sources) == 1:
            hits.append(("single_source", SIGNALS["single_source"][0]))
        total = sum(p for _, p in hits)
        level = _level(total)
        assessments.append(
            RiskAssessment(
                person=r.person,
                org=r.org,
                position=r.position,
                status=m.status,
                score=total,
                level=level,
                signals=tuple(hits),
                recommendation=_recommendation(m.status, level),
                sources=m.sources,
            )
        )
    return assessments


def review_queue(assessments: list[RiskAssessment]) -> list[RiskAssessment]:
    """人工复核队列：只收 conflict 项。复核结论回流是跨会话记忆的扩展位。"""
    return [a for a in assessments if a.status == MergeStatus.CONFLICT]


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
        records += extract_appointments(
            html_to_text((samples / f"{name}.html").read_text(encoding="utf-8")),
            source=name,
        )

    assessments = sorted(score(merge(records)), key=lambda a: -a.score)
    print("== 风险分级（评分卡可解释） ==")
    for a in assessments:
        sig = " + ".join(f"{name}({p})" for name, p in a.signals) or "无信号"
        print(
            f"  [{a.level}] {a.score:>2}分  {a.person} @{a.org}{a.position}"
            f"  <- {sig}  建议: {a.recommendation}"
        )

    queue = review_queue(assessments)
    print(f"\n== 人工复核队列（conflict 强制进队，共 {len(queue)} 条） ==")
    for a in queue:
        print(
            f"  {a.person} {a.position} @{a.org}"
            f"  来源冲突: {' vs '.join(a.sources)}  分数 {a.score}"
        )
