"""v0.5 三路召回：BM25 + 向量 + 图谱，min-max 归一化后加权 RRF 融合。

对应公司 ai-rag-new 的检索架构：每路 top-10，分数 min-max 归一化，
按名次做加权 RRF（k=60）取 top-8；任一路故障软降级，不拖死检索主链路。
"""

from __future__ import annotations

import math
import re
import zlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .embed import EmbeddingClient, cosine

DEFAULT_WEIGHTS = {"bm25": 0.4, "vector": 0.4, "graph": 0.2}
RRF_K = 60
ROUTE_TOP_K = 10
FUSED_TOP_K = 8


@dataclass(frozen=True)
class Chunk:
    id: str  # 与 Event 同款 "来源#L行号"，检索结果可反查证据行
    text: str
    source: str
    line_no: int


@dataclass(frozen=True)
class GraphLink:
    entity: str  # 人名或机构名
    source: str  # 实体出现的公告
    strength: float = 1.0


@dataclass
class SearchReport:
    query: str
    routes: dict[str, list[tuple[str, float]]] = field(default_factory=dict)
    fused: list[tuple[str, float]] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)


def tokenize(text: str) -> list[str]:
    """中文按字符二元组切分（免去->['免去']，河州市->['河州','州市']），
    英数连续段小写整词。零依赖的中文 BM25 分词。"""
    tokens: list[str] = []
    for m in re.finditer(r"[\u4e00-\u9fff]+|[A-Za-z0-9]+", text):
        seg = m.group()
        if re.fullmatch(r"[\u4e00-\u9fff]+", seg):
            tokens += [seg[i : i + 2] for i in range(len(seg) - 1)] or [seg]
        else:
            tokens.append(seg.lower())
    return tokens


class HashBigramEmbedding:
    """离线兜底：二元组 crc32 哈希稀疏向量。只保证链路可跑与结果确定性，
    无真语义能力——真语义召回需配置 DashScopeEmbedding。"""

    name = "local:hash-bigram"

    def embed(self, texts: list[str]) -> list[dict[int, float]]:
        out = []
        for t in texts:
            counts = Counter(tokenize(t))
            out.append({zlib.crc32(g.encode("utf-8")) % 65536: 1.0 + math.log(c)
                        for g, c in counts.items()})
        return out


class BM25:
    """自实现 BM25，参数对齐 ai-rag-new（k1=2.2, b=1.2）。"""

    def __init__(self, chunks: list[Chunk], k1: float = 2.2, b: float = 1.2):
        self.chunks = chunks
        self.k1, self.b = k1, b
        self.doc_tokens = [tokenize(c.text) for c in chunks]
        self.doc_len = [len(t) for t in self.doc_tokens]
        self.avgdl = sum(self.doc_len) / max(len(self.doc_len), 1)
        self.df: Counter[str] = Counter()
        for toks in self.doc_tokens:
            self.df.update(set(toks))
        self.N = len(chunks)

    def _idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        return math.log(1 + (self.N - df + 0.5) / (df + 0.5))

    def search(self, query: str, top_k: int = ROUTE_TOP_K) -> list[tuple[str, float]]:
        scores: dict[str, float] = {}
        q_terms = set(tokenize(query))
        for i, toks in enumerate(self.doc_tokens):
            tf = Counter(toks)
            s = 0.0
            for t in q_terms:
                f = tf.get(t, 0)
                if not f:
                    continue
                norm = f + self.k1 * (1 - self.b + self.b * self.doc_len[i] / self.avgdl)
                s += self._idf(t) * f * (self.k1 + 1) / norm
            if s > 0:
                scores[self.chunks[i].id] = s
        return sorted(scores.items(), key=lambda x: -x[1])[:top_k]


def vector_recall(
    query: str, chunks: list[Chunk], embedder: EmbeddingClient, top_k: int = ROUTE_TOP_K
) -> list[tuple[str, float]]:
    vecs = embedder.embed([query] + [c.text for c in chunks])
    qv, cvs = vecs[0], vecs[1:]
    scored = [(c.id, cosine(qv, cv)) for c, cv in zip(chunks, cvs)]
    scored = [(cid, s) for cid, s in scored if s > 0]
    return sorted(scored, key=lambda x: -x[1])[:top_k]


def graph_recall(
    query: str, links: list[GraphLink], chunks: list[Chunk], top_k: int = ROUTE_TOP_K
) -> list[tuple[str, float]]:
    """问题里命中图实体 -> 该实体关联公告的行进入候选；行内再出现实体加分。"""
    by_source: dict[str, list[Chunk]] = {}
    for c in chunks:
        by_source.setdefault(c.source, []).append(c)
    scores: dict[str, float] = {}
    for link in links:
        if link.entity and link.entity in query:
            for c in by_source.get(link.source, []):
                boost = 1.0 if link.entity in c.text else 0.6
                s = link.strength * boost
                if s > scores.get(c.id, 0.0):
                    scores[c.id] = s
    return sorted(scores.items(), key=lambda x: -x[1])[:top_k]


def min_max(pairs: list[tuple[str, float]]) -> list[tuple[str, float]]:
    if not pairs:
        return []
    vals = [s for _, s in pairs]
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-12:
        return [(cid, 1.0) for cid, _ in pairs]
    return [(cid, (s - lo) / (hi - lo)) for cid, s in pairs]


def weighted_rrf(
    ranked: dict[str, list[str]],
    weights: dict[str, float],
    k: int = RRF_K,
    top_k: int = FUSED_TOP_K,
) -> list[tuple[str, float]]:
    """RRF 按名次融合：score = Σ 权重/(k+名次)。名次制天然免疫各路分数量纲差异。"""
    fused: dict[str, float] = {}
    for route, ids in ranked.items():
        w = weights.get(route, 1.0)
        for rank, cid in enumerate(ids, start=1):
            fused[cid] = fused.get(cid, 0.0) + w / (k + rank)
    return sorted(fused.items(), key=lambda x: -x[1])[:top_k]


def search(
    query: str,
    chunks: list[Chunk],
    *,
    embedder: EmbeddingClient | None = None,
    graph_links: list[GraphLink] | None = None,
    weights: dict[str, float] | None = None,
    top_k: int = FUSED_TOP_K,
) -> SearchReport:
    weights = weights or DEFAULT_WEIGHTS
    bm25 = BM25(chunks)
    report = SearchReport(query=query)
    routes: dict[str, list[tuple[str, float]]] = {}

    try:
        routes["bm25"] = bm25.search(query)
    except Exception:
        report.degraded.append("bm25")
    if embedder is not None:
        try:
            routes["vector"] = vector_recall(query, chunks, embedder)
        except Exception:
            report.degraded.append("vector")
    if graph_links:
        try:
            routes["graph"] = graph_recall(query, graph_links, chunks)
        except Exception:
            report.degraded.append("graph")

    report.routes = {r: min_max(pairs) for r, pairs in routes.items()}
    ranked = {r: [cid for cid, _ in pairs] for r, pairs in report.routes.items()}
    report.fused = weighted_rrf(ranked, weights, top_k=top_k)
    return report


def build_chunks(corpus: list[tuple[str, str]]) -> list[Chunk]:
    """语料 [(来源, 文本)] -> 逐行分块，行号与抽取层一致，可反查证据。"""
    chunks: list[Chunk] = []
    for source, text in corpus:
        for no, line in enumerate(text.splitlines(), start=1):
            s = line.strip()
            if s and not s.startswith("#"):
                chunks.append(Chunk(id=f"{source}#L{no}", text=s, source=source, line_no=no))
    return chunks


LINKS_CYPHER = """\
MATCH (p:Person)-[:SUBJECT]->(:Event)<-[:RECORDS]-(n:Notice)
RETURN DISTINCT p.name AS entity, n.source AS source
UNION
MATCH (o:Org)<-[:AT]-(:Event)<-[:RECORDS]-(n:Notice)
RETURN DISTINCT o.name AS entity, n.source AS source
"""


def _load_corpus(records_source_dir: Path) -> tuple[list[Chunk], list]:
    from .collect import html_to_text
    from .extract import extract_appointments

    corpus, records = [], []
    txt = (records_source_dir / "notice-001.txt").read_text(encoding="utf-8")
    corpus.append(("notice-001", txt))
    records += extract_appointments(txt, source="notice-001")
    for name in ("notice-002", "notice-003", "notice-004", "notice-005", "notice-006"):
        text = html_to_text(
            (records_source_dir / f"{name}.html").read_text(encoding="utf-8")
        )
        corpus.append((name, text))
        records += extract_appointments(text, source=name)
    return build_chunks(corpus), records


if __name__ == "__main__":
    import os

    samples = Path(__file__).resolve().parent.parent.parent / "data" / "samples"
    chunks, records = _load_corpus(samples)

    embedder = HashBigramEmbedding()
    if os.environ.get("DASHSCOPE_API_KEY"):
        from .embed import DashScopeEmbedding

        embedder = DashScopeEmbedding()
    print(f"语料 {len(chunks)} 块 / {len(records)} 条记录 / 向量路: {embedder.name}")

    links: list[GraphLink] | None = None
    try:
        from .neo4j_store import Neo4jStore

        store = Neo4jStore()
        rows = store.query(LINKS_CYPHER)
        store.close()
        links = [GraphLink(r["entity"], r["source"]) for r in rows]
    except Exception:
        links = None
    if links is None:
        # 图谱路软降级：Neo4j 取不到时用抽取记录重建实体->来源索引
        links = [GraphLink(r.person, r.source) for r in records]
        links += [GraphLink(r.org, r.source) for r in records]
        print("（Neo4j 不可用，图谱路降级为抽取记录索引）")

    text_by_id = {c.id: c.text for c in chunks}
    for q in ("孙丽华的任免情况", "河州市发展和改革委员会的副主任", "疾控中心新任主任是谁"):
        report = search(q, chunks, embedder=embedder, graph_links=links)
        print(f"\n== 查询: {q} ==")
        for route, pairs in report.routes.items():
            top3 = "  ".join(f"{cid}({s:.2f})" for cid, s in pairs[:3])
            print(f"  [{route}] {top3}")
        if report.degraded:
            print(f"  软降级: {'、'.join(report.degraded)}")
        for cid, s in report.fused[:5]:
            print(f"  {s:.4f}  {cid}  {text_by_id[cid][:40]}")
