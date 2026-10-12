from risklist_kg_miner.rag import build_chunks, search
from risklist_kg_miner.rerank import RERANK_POOL, NoopRerank, parse_response


def test_parse_response_maps_scores_by_index():
    # rerank 接口按分数降序返回，index 指回原文档位置 -> 必须按 index 归位
    payload = {
        "output": {
            "results": [
                {"index": 2, "relevance_score": 0.31},
                {"index": 0, "relevance_score": 0.92},
            ]
        }
    }
    assert parse_response(payload, 3) == [0.92, 0.0, 0.31]


def test_noop_rerank_keeps_rrf_order():
    chunks = build_chunks(
        [("n", "任命 陈志远 为 河州市发展和改革委员会副主任；\n免去 孙丽华 的市卫生健康委员会副调研员；")]
    )
    base = search("陈志远", chunks)
    reranked = search("陈志远", chunks, reranker=NoopRerank())
    # 名次与 RRF 完全一致；分数换成了精排语义（全 0）——降级时只丢分不丢序
    assert [cid for cid, _ in reranked.fused] == [cid for cid, _ in base.fused]
    assert reranked.fused[0][1] == 0.0
    assert reranked.rerank == "noop"
    assert reranked.degraded == []


def test_rerank_pulls_pool_tail_into_top_k():
    # 12 行都含"陈志远" -> RRF 池 12 个。精排把 RRF 第 9 名（top-8 之外）
    # 顶到第 1：精排有权换序，这正是它存在的意义——precision 靠它修。
    lines = [f"任命 陈志远 为 机构{i}的职务{i}；" for i in range(12)]
    chunks = build_chunks([("n", "\n".join(lines))])
    full = search("陈志远", chunks, top_k=RERANK_POOL)
    tail_id = full.fused[8][0]
    text_by_id = {c.id: c.text for c in chunks}

    class BoostTail:
        name = "boost-tail"

        def rerank(self, query, documents):
            return [5.0 if d == text_by_id[tail_id] else 0.0 for d in documents]

    r = search("陈志远", chunks, reranker=BoostTail())
    assert len(r.fused) == 8
    assert r.fused[0][0] == tail_id
    assert r.rerank == "boost-tail"


def test_rerank_failure_soft_degrades_to_rrf_order():
    # 精排服务抖动 -> 记入 degraded，融合名次原样返回，检索不中断。
    chunks = build_chunks(
        [("n", "任命 陈志远 为 河州市发展和改革委员会副主任；\n免去 孙丽华 的市卫生健康委员会副调研员；")]
    )

    class Broken:
        name = "broken"

        def rerank(self, query, documents):
            raise RuntimeError("精排服务超时")

    r = search("陈志远", chunks, reranker=Broken())
    base = search("陈志远", chunks)
    assert "rerank:broken" in r.degraded
    assert r.fused == base.fused
    assert r.rerank == ""
