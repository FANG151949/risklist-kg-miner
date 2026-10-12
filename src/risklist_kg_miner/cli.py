"""v0.8 CLI：四个命令收拢全链路。

    uv run python -m risklist_kg_miner ingest            # 一键流水线（增量）
    uv run python -m risklist_kg_miner search "查询词"    # 三路召回 + 精排
    uv run python -m risklist_kg_miner evaluate          # 评测消融（教程七）
    uv run python -m risklist_kg_miner review            # 风险分级 + 复核队列
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def _store_or_none():
    try:
        from .neo4j_store import Neo4jStore

        return Neo4jStore()
    except Exception:
        return None


def _llm_or_none():
    if not os.environ.get("DEEPSEEK_API_KEY"):
        return None
    try:
        from .deepseek import default_client

        return default_client()
    except Exception:
        return None


def _cmd_ingest(args) -> int:
    from . import pipeline

    store = _store_or_none()
    if store is None:
        print("（Neo4j 驱动不可用：图谱步骤软降级跳过）")
    llm = _llm_or_none()
    if llm is None:
        print("（未检测到 DEEPSEEK_API_KEY：冲突走保守结论，直接转人工复核）")
    r = pipeline.run(store=store, llm=llm, force=args.force)

    print(f"\n处理 {len(r.processed)} 篇: {'、'.join(r.processed) or '无'}")
    if r.skipped:
        print(f"增量跳过 {len(r.skipped)} 篇（图内已有）: {'、'.join(r.skipped)}")
    for f in r.failures:
        print(f"失败隔离: {f}")
    print(f"抽取 {r.records} 条 -> 合并 {r.groups} 组（冲突 {r.conflicts}）")
    print(f"图谱落库: {'成功' if r.graph_applied else '未落库'}")
    for d in r.degraded:
        print(f"软降级: {d}")
    print(f"人工复核队列 {len(r.queue)} 条: {'、'.join(r.queue) or '空'}")
    return 0


def _cmd_search(args) -> int:
    from .rag import LINKS_CYPHER, GraphLink, HashBigramEmbedding, _load_corpus, search
    from .rerank import RERANK_POOL

    samples = Path(__file__).resolve().parent.parent.parent / "data" / "samples"
    chunks, records = _load_corpus(samples)

    embedder = HashBigramEmbedding()
    reranker = None
    if os.environ.get("DASHSCOPE_API_KEY"):
        from .embed import DashScopeEmbedding
        from .rerank import DashScopeRerank

        embedder = DashScopeEmbedding()
        reranker = DashScopeRerank()

    links = None
    store = _store_or_none()
    if store is not None:
        try:
            rows = store.query(LINKS_CYPHER)
            links = [GraphLink(r["entity"], r["source"]) for r in rows]
        except Exception:
            links = None
        finally:
            store.close()
    if links is None:
        links = [GraphLink(r.person, r.source) for r in records]
        links += [GraphLink(r.org, r.source) for r in records]
        print("（Neo4j 不可用，图谱路降级为抽取记录索引）")

    report = search(
        args.query, chunks, embedder=embedder, graph_links=links, reranker=reranker
    )
    text_by_id = {c.id: c.text for c in chunks}
    print(f"\n查询: {args.query}  向量路: {embedder.name}")
    for route, pairs in report.routes.items():
        top3 = "  ".join(f"{cid}({s:.2f})" for cid, s in pairs[:3])
        print(f"  [{route}] {top3}")
    if report.rerank:
        print(f"  [rerank] {report.rerank}（RRF top-{RERANK_POOL} 池重排取 top-8）")
    if report.degraded:
        print(f"  软降级: {'、'.join(report.degraded)}")
    for cid, s in report.fused:
        print(f"  {s:.4f}  {cid}  {text_by_id[cid][:40]}")
    return 0


def _cmd_evaluate(args) -> int:
    from .evals import _demo

    _demo()
    return 0


def _cmd_review(args) -> int:
    from . import pipeline
    from .validate import MergeStatus

    r = pipeline.run(store=None, llm=_llm_or_none())
    print("== 风险分级（评分卡可解释） ==")
    for a in sorted(r.assessments, key=lambda a: -a.score):
        sig = " + ".join(f"{name}({p})" for name, p in a.signals) or "无信号"
        print(
            f"  [{a.level}] {a.score:>2}分  {a.person} @{a.org}{a.position}"
            f"  <- {sig}  建议: {a.recommendation}"
        )
    queue = [a for a in r.assessments if a.status == MergeStatus.CONFLICT]
    print(f"\n== 人工复核队列（conflict 强制进队，共 {len(queue)} 条） ==")
    for a in queue:
        print(
            f"  {a.person} {a.position} @{a.org}"
            f"  来源冲突: {' vs '.join(a.sources)}  分数 {a.score}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="risklist-kg-miner",
        description="高风险人员名单挖掘与知识图谱系统（合成数据演示）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_ing = sub.add_parser("ingest", help="采集→抽取→合并→建图→评分 一键流水线")
    p_ing.add_argument("--force", action="store_true", help="忽略增量，全量重跑")
    p_ing.set_defaults(func=_cmd_ingest)

    p_search = sub.add_parser("search", help="三路召回 + 精排检索")
    p_search.add_argument("query")
    p_search.set_defaults(func=_cmd_search)

    p_eval = sub.add_parser("evaluate", help="评测消融（标注集见 data/eval）")
    p_eval.set_defaults(func=_cmd_evaluate)

    p_review = sub.add_parser("review", help="风险分级 + 人工复核队列")
    p_review.set_defaults(func=_cmd_review)

    args = parser.parse_args(argv)
    return args.func(args) or 0
