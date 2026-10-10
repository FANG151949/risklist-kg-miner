"""DeepSeek 客户端：OpenAI 兼容接口。

用法：
    1. 在 DeepSeek 开放平台创建 API key（platform.deepseek.com）
    2. export DEEPSEEK_API_KEY=sk-xxx，或写入仓库根目录 .env（已 gitignore）
    3. client = default_client()

注意：只发送合成数据，key 永不进入代码与 git 历史。
"""

from __future__ import annotations

import os

from .validate import LLMClient

BASE_URL = "https://api.deepseek.com/v1"


class DeepSeekClient:
    def __init__(self, model: str = "deepseek-chat", api_key: str | None = None):
        self.model = model
        key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            raise RuntimeError(
                "缺少 DEEPSEEK_API_KEY：请 export DEEPSEEK_API_KEY=sk-xxx "
                "或写入仓库根目录 .env（已在 .gitignore 中）"
            )
        from openai import OpenAI  # 延迟导入：没装 openai 包时只在真正调用时报错

        self._client = OpenAI(api_key=key, base_url=BASE_URL)

    def complete(self, prompt: str) -> str:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        return resp.choices[0].message.content or ""


def default_client() -> LLMClient:
    return DeepSeekClient()
