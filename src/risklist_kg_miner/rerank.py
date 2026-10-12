"""v0.8 精排（rerank）：RRF 名次融合只度量"多路共识"，不度量查询与文档的逐对相关度。

v0.7 消融显示 recall@8 已 100% 而 precision@8 仅 ~18%（top-8 六成是噪声）。
cross-encoder 把 查询+文档 拼在一起现场打分，相当于放弃 bi-encoder
"各自编码、向量可缓存"的效率换取逐对精度——只用在融合后的 top-20 小池子上，
代价可控。精排只能重排池内候选，救不了池外漏检：召回与精排各司其职。
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Protocol

RERANK_POOL = 20  # 精排候选池：RRF 取 top-20，精排后只留 top-8


class Reranker(Protocol):
    name: str

    def rerank(self, query: str, documents: list[str]) -> list[float]: ...


class NoopRerank:
    """离线兜底：不打分，全 0 返回。全 0 分数 + 稳定排序 = 退回 RRF 原名次。"""

    name = "noop"

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        return [0.0] * len(documents)


class DashScopeRerank:
    """阿里云百炼 gte-rerank：rerank 无 OpenAI 兼容接口，urllib 裸调零新依赖。"""

    name = "dashscope:gte-rerank-v2"
    ENDPOINT = (
        "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
    )

    def __init__(self, model: str = "gte-rerank-v2", api_key: str | None = None):
        self.model = model
        self.api_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not self.api_key:
            raise RuntimeError("缺少 DASHSCOPE_API_KEY")
        body = json.dumps(
            {
                "model": self.model,
                "input": {"query": query, "documents": documents},
                "parameters": {"return_documents": False, "top_n": len(documents)},
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            self.ENDPOINT,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return parse_response(json.loads(resp.read()), len(documents))


def parse_response(payload: dict, n: int) -> list[float]:
    """{"output":{"results":[{"index":1,"relevance_score":0.9},...]}}
    -> 按原文档序的分数列表（rerank 接口按分数降序返回，index 指回原位）。"""
    scores = [0.0] * n
    for r in payload["output"]["results"]:
        scores[r["index"]] = r["relevance_score"]
    return scores
