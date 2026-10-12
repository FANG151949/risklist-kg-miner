# risklist-kg-miner

高风险人员名单挖掘与知识图谱系统——围绕核保、理赔场景的高风险人员识别，实现"自动化采集 + 规则初筛 + LLM 校验 + 图谱沉淀 + 三路召回检索"的完整链路。

## 技术路线

- 多 agent 编排：coordinator + 采集 / 校验 / 建图 / 评分专职 agent（手写编排，不依赖 LangChain）
- 规则引擎：确定性纯函数规则优先，LLM 兜底，冲突取更保守结论
- LLM：DeepSeek 裁决冲突（OpenAI 兼容接口），向量召回用阿里云百炼 text-embedding-v3；密钥一律环境变量/`.env`（已 gitignore），只发送合成数据
- RAG：BM25 + 向量 + 知识图谱三路召回，min-max 归一化 + 加权 RRF 融合，任一路软降级
- Rerank：RRF top-20 候选池交给百炼 gte-rerank-v2（cross-encoder 逐对打分）收回 top-8，离线 noop 兜底、失败软降级回 RRF 名次；实测 MRR 0.932→1.000（11 查询首条证据全排第 1）且 recall 不掉，precision@8 受语料密度上限制约（每查询仅 ~1.4 条相关行）
- 图谱：Neo4j 沉淀 人-机构-职务-事件 关系链，支持一人多职、任免历史追踪

## 数据红线

仓库内全部数据均为**合成数据**：虚构人名、虚构机构、手写公告文本与 HTML 样本。不来源于任何真实政府网站或真实人员名单。

## 快速开始

```bash
uv run python -m risklist_kg_miner ingest            # 一键流水线（增量）
uv run python -m risklist_kg_miner search "查询词"    # 三路召回 + 精排
uv run python -m risklist_kg_miner evaluate          # 评测消融
uv run python -m risklist_kg_miner review            # 风险分级 + 复核队列
```

不配任何 key 全程离线可跑（哈希向量 + noop 精排 + 保守合并）；配置 `DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY`（写入 `.env`，已 gitignore）后自动启用 LLM 裁决、真嵌入与 gte-rerank 精排。各模块也可单独运行（`python -m risklist_kg_miner.rag` 等）。

## 版本路线（需求驱动）

| 版本 | 需求 | 交付 |
|---|---|---|
| v0.1 | 人工读任免公示太慢 | 公告文本 → 规则抽取 人名/机构/职务/时间 + 行号证据 |
| v0.2 | 来源是异构 HTML | collector agent + reading loop |
| v0.3 ✅ | 多来源记录冲突 | validator agent：规则优先 + LLM 兜底 + 取保守结论 |
| v0.4 ✅ | 关系查询效率 | Neo4j：事件流水（留痕）+ 任职状态（查询）双写，MERGE 幂等落库 |
| v0.5 ✅ | 语料覆盖 + 自然语言检索 | 版式适配（表格/叙述）+ 机构归一化旧边清理 + 三路召回 RAG（自实现 BM25 + 百炼向量 + 图谱实体，加权 RRF k=60） |
| v0.6 ✅ | 名单分级 | 评分卡 scorer（信号/分值/理由可解释）+ 人工复核队列（conflict 强制进队，复核结论回流为预留记忆接口） |
| v0.7 ✅ | 可信度证明 | 标注集（14 记录/10 查询）+ 抽取层 P/R 100%/100% + 检索消融：真嵌入救缩略语（recall 90→100%），图谱提排序（MRR +0.05）；rerank 列入 v0.8+ |
| v0.8 ✅ | 可用性 | rerank 精排（MRR 0.932→1.000）+ pipeline 封装（增量：图内公告跳过、历史从 :Event 读回参与合并，跨公告冲突不漏检；单篇失败隔离；全程软降级）+ CLI 四命令（ingest/search/evaluate/review） |
| v0.9 | 发布 | README、CI、pip 安装 |

## 开发方式

自下而上：从一个简单需求开始，每版先讲需求（谁提的、原来怎么痛），再最小实现、跑起来看效果、暴露痛点引出下一版。不预设完美骨架。
