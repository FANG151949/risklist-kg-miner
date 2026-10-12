"""v0.3 校验与合并：多源记录去重、冲突裁决、LLM 兜底降级。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from .extract import AppointmentRecord


class LLMClient(Protocol):
    """LLM 抽象接口：测试用 FakeLLM，生产用 QwenClient。"""

    def complete(self, prompt: str) -> str: ...


class MergeStatus:
    CONFIRMED = "confirmed"  # 单源记录，无需合并
    MERGED = "merged"  # 多源一致（含简称/全称），已合并
    CONFLICT = "conflict"  # 多源矛盾，规则+LLM 都定不了，转人工复核


@dataclass(frozen=True)
class MergeResult:
    record: AppointmentRecord
    status: str
    note: str
    sources: tuple[str, ...]


class FakeLLM:
    """测试专用的假 LLM：返回预设回复，同时记录收到的 prompt 便于断言。"""

    def __init__(self, reply: str):
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


def _org_matches(a: str, b: str) -> bool:
    """机构全称与简称视为同一机构。

    '河州市卫生健康委员会' vs '市卫生健康委员会'：全称以后缀形式包含简称。
    """
    return a == b or a.endswith(b) or b.endswith(a)


def _tag(r: AppointmentRecord) -> str:
    return r.source or f"L{r.line_no}"


def _conservative_fallback(records: list[AppointmentRecord]) -> MergeResult:
    """LLM 不可用时的保守结论：免去优先（宁可漏报不误报），其次取最新一条。"""
    removed = [r for r in records if r.action == "免去"]
    kept = removed[0] if removed else records[-1]
    return MergeResult(
        record=kept,
        status=MergeStatus.CONFLICT,
        note="LLM 不可用，取保守结论，转人工复核",
        sources=tuple(_tag(r) for r in records),
    )


def _adjudicate(records: list[AppointmentRecord], llm: LLMClient) -> MergeResult:
    candidates = "；".join(
        f"候选{i + 1}（{_tag(r)} 第{r.line_no}行）：{r.action}{r.person}的{r.org}{r.position}职务"
        for i, r in enumerate(records)
    )
    prompt = (
        "你是人事公告核验员。同一人同一职务在不同公告中说法矛盾，"
        "请判断哪一条是最新可信的，并只输出 JSON："
        '{"latest": 候选编号, "reason": "一句话理由"}\n'
        f"{candidates}"
    )
    # v0.8 失败重试：网络抖动/坏输出重试一次，两次都失败才走保守回退
    for _ in range(2):
        try:
            data = json.loads(llm.complete(prompt))
            chosen = records[int(data["latest"]) - 1]
            return MergeResult(
                record=chosen,
                status=MergeStatus.MERGED,
                note=f"LLM 判定以{_tag(chosen)}为准：{data['reason']}",
                sources=tuple(_tag(r) for r in records),
            )
        except Exception:
            continue
    return _conservative_fallback(records)


def merge(
    records: list[AppointmentRecord], llm: LLMClient | None = None
) -> list[MergeResult]:
    """按 (人, 职务) 分组：一致的合并，矛盾的裁决，裁决不了的人工复核。"""
    groups: dict[tuple[str, str], list[AppointmentRecord]] = {}
    for r in records:
        groups.setdefault((r.person, r.position), []).append(r)

    results = []
    for rs in groups.values():
        if len(rs) == 1:
            results.append(
                MergeResult(rs[0], MergeStatus.CONFIRMED, "单源记录", (_tag(rs[0]),))
            )
            continue
        actions = {r.action for r in rs}
        longest = max(rs, key=lambda r: len(r.org))
        if len(actions) == 1 and all(_org_matches(longest.org, r.org) for r in rs):
            results.append(
                MergeResult(
                    longest,
                    MergeStatus.MERGED,
                    f"{len(rs)} 源一致，机构写法合并取全称",
                    tuple(_tag(r) for r in rs),
                )
            )
        elif llm is not None:
            results.append(_adjudicate(rs, llm))
        else:
            results.append(_conservative_fallback(rs))
    return results


if __name__ == "__main__":
    from pathlib import Path

    from .collect import html_to_text
    from .extract import extract_appointments

    samples = Path(__file__).resolve().parent.parent.parent / "data" / "samples"
    records = extract_appointments(
        (samples / "notice-001.txt").read_text(encoding="utf-8"), source="notice-001"
    )
    for name in ("notice-002", "notice-003", "notice-004", "notice-005", "notice-006"):
        html = (samples / f"{name}.html").read_text(encoding="utf-8")
        records += extract_appointments(html_to_text(html), source=name)

    flags = {MergeStatus.CONFIRMED: "OK ", MergeStatus.MERGED: "MERGE", MergeStatus.CONFLICT: "WARN"}
    results = merge(records)
    for m in results:
        print(
            f"[{flags[m.status]}] {m.record.person} {m.record.action}"
            f"{m.record.org}{m.record.position}"
            f"  <- {m.note}（来源：{'、'.join(m.sources)}）"
        )
    print(f"共 {len(records)} 条原始记录 -> {len(results)} 条合并结论")
