from risklist_kg_miner.evals import (
    eval_extraction,
    eval_merge,
    eval_retrieval,
    load_corpus,
    load_ground_truth,
)
from risklist_kg_miner.rag import GraphLink, HashBigramEmbedding
from risklist_kg_miner.validate import merge

TWO_WAY = {"bm25": 0.5, "vector": 0.5}
THREE_WAY = {"bm25": 0.4, "vector": 0.4, "graph": 0.2}


def _setup():
    truth = load_ground_truth()
    chunks, records = load_corpus()
    links = [GraphLink(r.person, r.source) for r in records]
    links += [GraphLink(r.org, r.source) for r in records]
    return truth, chunks, records, links


def test_ground_truth_ids_exist_in_corpus():
    truth, chunks, records, _ = _setup()
    ids = {c.id for c in chunks}
    for item in truth["queries"]:
        for cid in item["relevant"]:
            assert cid in ids, f"标注引用了语料中不存在的行: {cid}"
    assert len(truth["records"]) == 14


def test_extraction_eval_is_perfect_on_corpus():
    truth, chunks, records, _ = _setup()
    precision, recall, diffs = eval_extraction(records, truth)
    assert (precision, recall) == (1.0, 1.0)
    assert diffs == []


def test_extraction_eval_catches_missing_record():
    truth, _, records, _ = _setup()
    dropped = [r for r in records if not (r.person == "冯建华" and r.source == "notice-005")]
    precision, recall, diffs = eval_extraction(dropped, truth)
    assert recall < 1.0
    assert any("冯建华" in str(d) for d in diffs)


def test_merge_eval_passes_on_corpus():
    truth, _, records, _ = _setup()
    assert eval_merge(merge(records), truth_groups=11, truth_conflicts=1)


def test_offline_ablation_numbers():
    # 离线消融基线（哈希向量）：两路 recall 90%（卫健委查询漏检）、
    # 图谱路不补 recall 但提升 MRR 0.850 -> 0.900。
    truth, chunks, records, links = _setup()
    emb = HashBigramEmbedding()

    two = eval_retrieval(truth["queries"], chunks, None, emb, TWO_WAY)
    three = eval_retrieval(truth["queries"], chunks, links, emb, THREE_WAY)

    assert two.recall == 0.9
    assert three.recall == 0.9
    assert two.misses == ("notice-001#L15", "notice-003#L3")  # 唯一漏检：卫健委缩略语查询
    assert three.misses == two.misses  # 图谱实体是全称，问题里是简称 -> 图谱路同样救不了
    assert three.mrr > two.mrr


def test_semantic_gap_query_is_the_only_miss():
    # 漏检的两行都属于"卫健委的人事变动"——缩略语语义鸿沟的真嵌入价值证据。
    truth, chunks, records, links = _setup()
    weijianwei = [q for q in truth["queries"] if q["query"] == "卫健委的人事变动"]
    assert len(weijianwei) == 1
    m = eval_retrieval(weijianwei, chunks, None, HashBigramEmbedding(), TWO_WAY)
    assert m.recall == 0.0
    assert set(m.misses) == {"notice-001#L15", "notice-003#L3"}
