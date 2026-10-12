"""v0.7 评测与消融：抽取层 P/R、检索层 P@k/R@k/MRR、两路 vs 三路消融。

合成语料让我们握有全部真相（data/eval/ground_truth.json），
评测因此可以完全离线确定性运行；真嵌入模式用于量化语义召回增益。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .collect import html_to_text
from .extract import AppointmentRecord, extract_appointments
from .rag import Chunk, GraphLink, build_chunks, search
from .validate import MergeResult, MergeStatus, merge

EVAL_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "eval"
SAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "samples"


@dataclass(frozen=True)
class RetrievalMetrics:
    recall: float
    precision: float
    mrr: float
    misses: tuple[str, ...]  # 未进 top-k 的相关行


def load_ground_truth(path: Path | None = None) -> dict:
    with (path or EVAL_DIR / "ground_truth.json").open(encoding="utf-8") as f:
        return json.load(f)


def load_corpus(samples_dir: Path | None = None) -> tuple[list[Chunk], list[AppointmentRecord]]:
    samples_dir = samples_dir or SAMPLES_DIR
    corpus, records = [], []
    txt = (samples_dir / "notice-001.txt").read_text(encoding="utf-8")
    corpus.append(("notice-001", txt))
    records += extract_appointments(txt, source="notice-001")
    for name in ("notice-002", "notice-003", "notice-004", "notice-005", "notice-006"):
        text = html_to_text((samples_dir / f"{name}.html").read_text(encoding="utf-8"))
        corpus.append((name, text))
        records += extract_appointments(text, source=name)
    return build_chunks(corpus), records


def eval_extraction(records: list[AppointmentRecord], truth: dict) -> tuple[float, float, list]:
    """按 (source, action, person, org, position) 五元组比对。"""
    def key(r) -> tuple:
        return (r.source, r.action, r.person, r.org, r.position)
    truth_keys = {(t["source"], t["action"], t["person"], t["org"], t["position"]) for t in truth["records"]}
    pred_keys = {key(r) for r in records}
    tp = truth_keys & pred_keys
    missed = [k for k in truth_keys - pred_keys]
    extra = [k for k in pred_keys - truth_keys]
    precision = len(tp) / len(pred_keys) if pred_keys else 0.0
    recall = len(tp) / len(truth_keys) if truth_keys else 0.0
    return precision, recall, missed + extra


def eval_retrieval(
    queries: list[dict],
    chunks: list[Chunk],
    links: list[GraphLink] | None,
    embedder,
    weights: dict[str, float],
    top_k: int = 8,
) -> RetrievalMetrics:
    recalls, precs, rrs, misses_all = [], [], [], []
    for item in queries:
        report = search(
            item["query"], chunks, embedder=embedder,
            graph_links=links, weights=weights, top_k=top_k,
        )
        retrieved = [cid for cid, _ in report.fused]
        relevant = set(item["relevant"])
        hits = [cid for cid in retrieved if cid in relevant]
        recalls.append(len(hits) / len(relevant))
        precs.append(len(hits) / len(retrieved) if retrieved else 0.0)
        rank = next((i for i, cid in enumerate(retrieved, 1) if cid in relevant), None)
        rrs.append(1.0 / rank if rank else 0.0)
        misses_all += [cid for cid in item["relevant"] if cid not in retrieved]
    n = len(queries)
    return RetrievalMetrics(
        recall=sum(recalls) / n, precision=sum(precs) / n,
        mrr=sum(rrs) / n, misses=tuple(misses_all),
    )


def eval_merge(results: list[MergeResult], truth_groups: int, truth_conflicts: int) -> bool:
    conflicts = [m for m in results if m.status == MergeStatus.CONFLICT]
    return len(results) == truth_groups and len(conflicts) == truth_conflicts


def _demo() -> None:
    import os

    from .rag import HashBigramEmbedding

    truth = load_ground_truth()
    chunks, records = load_corpus()
    links = [GraphLink(r.person, r.source) for r in records]
    links += [GraphLink(r.org, r.source) for r in records]

    print(f"评测集: {len(truth['records'])} 条记录真相 / {len(truth['queries'])} 个标注查询 / 语料 {len(chunks)} 行")

    p, r, diffs = eval_extraction(records, truth)
    print(f"\n== 抽取层 ==  precision={p:.2%}  recall={r:.2%}", end="")
    print(f"  差异: {diffs}" if diffs else "  与标注完全一致")

    merged = merge(records)
    ok = eval_merge(merged, truth_groups=11, truth_conflicts=1)
    print(f"== 合并层 ==  11 组结论 / 1 条冲突: {'通过' if ok else '不通过'}")

    two_way_w = {"bm25": 0.5, "vector": 0.5}
    three_way_w = {"bm25": 0.4, "vector": 0.4, "graph": 0.2}
    hash_emb = HashBigramEmbedding()
    live = bool(os.environ.get("DASHSCOPE_API_KEY"))
    if live:
        from .embed import DashScopeEmbedding
        real_emb = DashScopeEmbedding()

    print("\n== 检索层消融（top-8） ==")
    print(f"{'配置':<28}{'recall@8':>10}{'precision@8':>13}{'MRR':>8}")
    for label, emb, ls, w in [
        ("两路·离线哈希向量", hash_emb, None, two_way_w),
        ("三路·离线哈希+图谱", hash_emb, links, three_way_w),
        *([("两路·百炼真嵌入", real_emb, None, two_way_w),
           ("三路·百炼真嵌入+图谱", real_emb, links, three_way_w)] if live else []),
    ]:
        m = eval_retrieval(truth["queries"], chunks, ls, emb, w)
        miss = f"  漏检: {m.misses}" if m.misses else ""
        print(f"{label:<28}{m.recall:>9.0%}{m.precision:>12.0%}{m.mrr:>8.3f}{miss}")
    if not live:
        print("（未检测到 DASHSCOPE_API_KEY，真嵌入两行未跑；用 --env-file .env 复跑可补齐）")


if __name__ == "__main__":
    _demo()
