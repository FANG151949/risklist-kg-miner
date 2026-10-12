"""向量路嵌入协议 + 百炼真实现。离线哈希兜底实现见 rag.py（与分词同处）。

统一用稀疏表示 {维度下标: 值}：百炼稠密向量转为下标字典，
两个实现共用 embed.cosine，维度差异不影响融合层。
"""

from __future__ import annotations

import math
import os
from typing import Protocol

DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class EmbeddingClient(Protocol):
    """与 validate.LLMClient 同款思路：测试可替换、生产可切换。"""

    name: str

    def embed(self, texts: list[str]) -> list[dict[int, float]]: ...


class DashScopeEmbedding:
    """阿里云百炼 text-embedding-v3（OpenAI 兼容接口），需 DASHSCOPE_API_KEY。"""

    name = "dashscope:text-embedding-v3"

    def __init__(self, model: str = "text-embedding-v3", api_key: str | None = None):
        key = api_key or os.environ.get("DASHSCOPE_API_KEY")
        if not key:
            raise RuntimeError(
                "缺少 DASHSCOPE_API_KEY：请在百炼控制台创建并写入 .env（已 gitignore）"
            )
        from openai import OpenAI  # 延迟导入：未配置 key 时完全不加载

        self._client = OpenAI(api_key=key, base_url=DASHSCOPE_BASE_URL)
        self.model = model

    def embed(self, texts: list[str]) -> list[dict[int, float]]:
        out: list[dict[int, float]] = []
        for i in range(0, len(texts), 10):  # text-embedding-v3 单批上限 10 条
            resp = self._client.embeddings.create(
                model=self.model, input=texts[i : i + 10]
            )
            out += [{j: v for j, v in enumerate(d.embedding)} for d in resp.data]
        return out


def cosine(a: dict[int, float], b: dict[int, float]) -> float:
    if len(b) < len(a):
        a, b = b, a
    dot = sum(v * b.get(k, 0.0) for k, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if not na or not nb:
        return 0.0
    return dot / (na * nb)
