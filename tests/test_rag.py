from risklist_kg_miner.embed import cosine
from risklist_kg_miner.rag import (
    BM25,
    GraphLink,
    HashBigramEmbedding,
    build_chunks,
    graph_recall,
    min_max,
    search,
    tokenize,
    weighted_rrf,
)


def test_tokenize_bigrams():
    # 中文连续段做滑动二元组（跨词重叠是特性：'市财' 仍能命中 '市财政局'）
    assert tokenize("免去河州市财政局局长") == [
        "免去", "去河", "河州", "州市", "市财", "财政", "政局", "局局", "局长",
    ]
    assert tokenize("Neo4j v5") == ["neo4j", "v5"]


def test_bm25_ranks_exact_name_top():
    chunks = build_chunks([("notice-001", "任命 陈志远 为 河州市发展和改革委员会副主任；\n免去 孙丽华 的市卫生健康委员会副调研员；")])
    hits = BM25(chunks).search("孙丽华")
    assert hits[0][0] == "notice-001#L2"


def test_graph_recall_maps_entity_to_sources():
    chunks = build_chunks([
        ("notice-001", "免去 孙丽华 的市卫生健康委员会副调研员；"),
        ("notice-003", "| 孙丽华 | 免去 | 河州市卫生健康委员会 | 副调研员"),
        ("notice-002", "任命 罗建军 为 江州市商务局副局长；"),
    ])
    links = [GraphLink("孙丽华", "notice-001"), GraphLink("孙丽华", "notice-003")]
    hits = graph_recall("孙丽华的任免情况", links, chunks)
    ids = [cid for cid, _ in hits]
    assert "notice-001#L1" in ids and "notice-003#L1" in ids
    assert not any(cid.startswith("notice-002") for cid in ids)
    # 行内出现实体的行分更高
    scores = dict(hits)
    assert scores["notice-001#L1"] > scores.get("notice-002#L1", 0)


def test_min_max_normalizes():
    assert min_max([("a", 2.0), ("b", 4.0)]) == [("a", 0.0), ("b", 1.0)]
    assert min_max([("a", 3.0)]) == [("a", 1.0)]
    assert min_max([]) == []


def test_weighted_rrf_favors_multi_route_chunks():
    fused = weighted_rrf(
        {"bm25": ["x", "a"], "vector": ["x", "b"]},
        {"bm25": 0.4, "vector": 0.4},
    )
    assert fused[0][0] == "x"  # 两路都命中的 x 压过单路冠军


def test_search_soft_degrades_when_vector_fails():
    chunks = build_chunks([("n", "任命 陈志远 为 河州市发展和改革委员会副主任；")])

    class BrokenEmbedder:
        name = "broken"

        def embed(self, texts):
            raise RuntimeError("网络抖动")

    report = search("陈志远", chunks, embedder=BrokenEmbedder())
    assert report.degraded == ["vector"]
    assert report.fused and report.fused[0][0] == "n#L1"


def test_hash_embedding_is_deterministic():
    e = HashBigramEmbedding()
    a1, a2 = e.embed(["河州市卫生健康委员会"])[0], e.embed(["河州市卫生健康委员会"])[0]
    assert a1 == a2
    assert cosine(a1, a2) == 1.0


def test_search_end_to_end_offline():
    # 无向量路、图谱路用离线索引：问陈志远，融合结果应同时覆盖
    # 任命公告行与免职公告行（多来源证据聚合）。
    chunks = build_chunks([
        ("notice-001", "任命 陈志远 为 河州市发展和改革委员会副主任；"),
        ("notice-006", "免去陈志远的河州市发展和改革委员会副主任职务。"),
        ("notice-002", "任命 罗建军 为 江州市商务局副局长；"),
    ])
    links = [GraphLink("陈志远", "notice-001"), GraphLink("陈志远", "notice-006")]
    report = search("陈志远的任免情况", chunks, graph_links=links)
    ids = [cid for cid, _ in report.fused]
    assert "notice-001#L1" in ids and "notice-006#L1" in ids
    assert "notice-002#L1" not in ids[:2]
