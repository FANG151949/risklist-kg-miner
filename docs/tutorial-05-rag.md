# 教程 05：v0.5 版式适配 + 三路召回 RAG

## 需求（谁提的、原来怎么痛）

v0.4 验收后业务同事提了三件事：

1. "卫健委、交通局发来的公告是**表格**和**叙述文**，你们系统只认'任命 X 为…'
   一种句式——一半公告进不了系统。"
2. "孙丽华在两个公告里机构写法不一样，图里挂了两个机构，到底哪个是真的？"
3. "我能不能直接**问一句**'孙丽华的任免情况'，把相关公告原文都找出来？"

**验收标准**：三种版式全部能抽取；机构简称/全称归一化后图里一人一职一条边；
自然语言问题 -> 三路召回 -> 融合 top-8，且任何一路挂了检索不挂。

## 步骤 1：版式适配（打开 src/risklist_kg_miner/extract.py）

抽取层从"一条正则"变成**模式注册表**，每行文本按 A→B→C 顺序尝试：

| 版式 | 样例 | 模式 |
|---|---|---|
| A 段落式 | `任命 陈志远 为 河州市发展和改革委员会副主任；` | 整行 match |
| B 表格式 | `\| 孙丽华 \| 免去 \| 河州市卫生健康委员会 \| 副调研员` | 整行 match（注意采集器给单元格加了前导 `\|`） |
| C 叙述式 | `周晓梅同志任…副局长`、`免去吴国强同志的…职务`、`冯建华不再担任…职务` | 句内 search，动作词是固定值 |

跑一下看效果：

```bash
uv run python -m risklist_kg_miner.collect data/samples/notice-003.html
uv run pytest -q
```

`test_collect.py` 里三条"yields_nothing_yet"测试已**翻面**为
"extracts_records"——它们当年钉死的是痛点现状，痛点解决后测试改钉新承诺。
这正是"测试断言业务承诺"的含义：承诺变了，测试跟着变，而不是删掉。

## 步骤 2：机构归一化 + 旧边清理

版式适配后孙丽华有了两条记录（001 简称 / 003 全称），动作一致、机构是
简称/全称——`merge()` 的既有规则直接合并取全称（v0.3 的 `_org_matches`）。

但图里有个新坑：**MERGE 只保证目标边存在，不清理多余边**。孙丽华之前
在"市卫生健康委员会"上挂过 STAFF 边，归一化后这条旧边会残留——同一人
在两个机构各一条任职状态，业务方看到就懵了。解法（graph_model.py）：

```
每条合并结论 -> 两个 op：
  1. CLEAN_STALE_STAFF：按 (人, 职位) 精确删除非目标机构上的旧边
  2. UPSERT_STAFF：MERGE 规范机构的新边
```

清理按职位限定，不会误伤"一人多职"的合法场景。重跑落库验证：

```bash
uv run python -m risklist_kg_miner.neo4j_store   # 36 条操作
```

然后 Browser 里查（应该只有一行，机构是全称）：

```cypher
MATCH (p:Person {name: '孙丽华'})-[r:STAFF]->(o) RETURN o.name, r.status, r.sources;
```

v0.4 教程里"查河州市卫健委 STAFF 为空"的痛点演示，现在返回孙丽华——闭环。

## 步骤 3：三路召回架构（对照公司 ai-rag-new）

```
问题 ──┬─> BM25 路     自实现，k1=2.2 b=1.2，中文滑动二元组分词（零依赖）
       ├─> 向量路      EmbeddingClient 协议：百炼 text-embedding-v3 / 离线哈希兜底
       └─> 图谱路      问题命中图实体（人/机构名）-> 实体关联公告行
              每路 top-10 -> min-max 归一化 -> 加权 RRF(k=60) -> top-8
              任一路异常 -> 软降级为空，检索不挂（D4）
```

三个关键决策（面试高频）：

- **为什么三路**：BM25 抓精确词（人名、机构名），向量抓语义（"免职"≈"不再
  担任"），图谱抓结构化关联（问到人名直接沿边找到关联公告）。三路失效面
  不同——demo 里问"疾控中心新任主任"，图谱路为空（图里实体是全称"河州市
  疾病预防控制中心"，问题里是简称"疾控中心"，字面包含判断失败），但
  BM25 的二元组照样命中。
- **为什么 RRF 而不是分数加权平均**：各路分数量纲完全不可比（BM25 是
  IDF 加权词频，向量是余弦，图谱是跳数倒数）。RRF 只用**名次**：
  `score = Σ 权重/(60+名次)`，天然免疫量纲差异。
- **为什么留 min-max**：归一化分数不进 RRF，但保留在报告里——它让
  "哪一路对这个结果贡献大"变得可比，v0.7 的消融实验要用。

权重默认 `bm25=0.4, vector=0.4, graph=0.2`：图谱是召回增益，不当主力。

## 步骤 4：跑 demo

```bash
uv run python -m risklist_kg_miner.rag
```

预期输出（向量路未配百炼 key 时用离线哈希兜底）：

```
语料 40 块 / 14 条记录 / 向量路: local:hash-bigram

== 查询: 孙丽华的任免情况 ==
  [bm25] notice-003#L1(1.00)  notice-003#L3(0.60)  notice-001#L15(0.55)
  [vector] notice-003#L1(1.00)  notice-003#L3(0.44)  notice-001#L15(0.41)
  [graph] notice-001#L15(1.00)  notice-001#L5(0.00)  notice-001#L6(0.00)
  0.0160  notice-001#L15  免去 孙丽华 的 市卫生健康委员会副调研员 职务。
  ...
```

看第一行融合结果：`notice-001#L15` 同时被三路命中（图谱路第 1 + BM25/向量
第 3），RRF 分数最高——**多路共识压过单路冠军**。chunk id 与 Event 同款
"来源#L行号"，检索结果直接反查证据行（v0.1 行号承诺的复利）。

## 步骤 4b：RRF 融合手算全程（以真实语料为例）

查询：`河州市发展和改革委员会的副主任`。三路各回 top-10（名次如下）：

| 名次 | bm25 路 (0.4) | 向量路 (0.4) | 图谱路 (0.2) |
|---|---|---|---|
| 1 | 001#L11 陈志远任命行 | 001#L11 | 001#L11 |
| 2 | 006#L3 陈志远免职行 | 006#L3 | 006#L3 |
| 3 | 003#L3 孙丽华表格行 | 003#L3 | 001#L5（标题行） |
| 4 | 003#L4 郑海涛表格行 | 001#L21 | 001#L6 |
| 5 | 001#L15 | 003#L4 | 001#L8 |
| 6 | 001#L21 | 006#L2 | 001#L9 |
| 7 | 006#L2 | 001#L5 | 001#L12 |
| 8 | 001#L5（标题行） | 004#L1 | 001#L13 |
| 9 | 004#L1 | 005#L1 | 001#L14 |
| 10 | 005#L1 | 001#L12 | 001#L15 |

对每个候选文档套公式 `score = Σ 权重/(60+名次)`（没进某路 top-10 该路贡献 0）：

```
001#L11 = 0.4/61 + 0.4/61 + 0.2/61 = 0.01639   三路全部第1
006#L3  = 0.4/62 + 0.4/62 + 0.2/62 = 0.01613   三路全部第2
001#L5  = 0.4/68 + 0.4/67 + 0.2/63 = 0.01503   三路都命中，但名次都靠后
003#L3  = 0.4/63 + 0.4/63        = 0.01270   只有两路，缺图谱路
```

融合 top-8（实测）：

| 融合分 | 文档 | 说明 |
|---|---|---|
| 0.01639 | 001#L11 | 任命行，三路共识第一 |
| 0.01613 | 006#L3 | 免职行，三路共识第二 |
| 0.01503 | 001#L5 | **逆袭**：单路名次全是 7-8，但三路全命中 |
| 0.01270 | 003#L3 | 单路第 3 ×2，被上面三路共识压过 |
| ... | ... | 后 4 名同理 |

两个看点：

1. **001#L5（公告标题）逆袭 003#L3**：前者每路只排第 7-8，后者两路排第 3——
   但 L5 三路全命中累加 0.01503 > L5 缺图谱路只有 0.01270。RRF 奖励
   **跨方法共识**：一个文档如果"关键词匹配、语义相似、图关联"三种视角
   都觉得沾边，它大概率真的相关，哪怕每种视角都不突出。
2. **量纲免疫**：图谱路分数只有 1.0/0.0 两档（跳数式打分），bm25 是
   IDF 加权词频，向量是余弦——直接加权平均根本没法比；RRF 只看名次，
   三套量纲完全不同的分数在同一公式里公平竞争。

复现这个手算（含每文档的逐路拆项）：

```bash
uv run python - <<'EOF'
from pathlib import Path
from risklist_kg_miner.collect import html_to_text
from risklist_kg_miner.rag import (BM25, GraphLink, HashBigramEmbedding, build_chunks,
                                   vector_recall, graph_recall, DEFAULT_WEIGHTS, LINKS_CYPHER)
from risklist_kg_miner.neo4j_store import Neo4jStore

samples = Path("data/samples")
corpus = [("notice-001", (samples/"notice-001.txt").read_text(encoding="utf-8"))]
for n in ("notice-002","notice-003","notice-004","notice-005","notice-006"):
    corpus.append((n, html_to_text((samples/f"{n}.html").read_text(encoding="utf-8"))))
chunks = build_chunks(corpus)
s = Neo4jStore()
links = [GraphLink(r["entity"], r["source"]) for r in s.query(LINKS_CYPHER)]
s.close()

q = "河州市发展和改革委员会的副主任"
routes = {"bm25": BM25(chunks).search(q),
          "vector": vector_recall(q, chunks, HashBigramEmbedding()),
          "graph": graph_recall(q, links, chunks)}
rank_of = {r: {cid: i for i, (cid, _) in enumerate(p, 1)} for r, p in routes.items()}
ids = []
for p in routes.values():
    ids += [c for c, _ in p if c not in ids]
rows = []
for c in ids:
    terms = [f"{w}/(60+{rank_of[r][c]})" for r, w in DEFAULT_WEIGHTS.items() if c in rank_of[r]]
    total = sum(w/(60+rank_of[r][c]) for r, w in DEFAULT_WEIGHTS.items() if c in rank_of[r])
    rows.append((total, c, terms))
for t, c, terms in sorted(rows, reverse=True)[:8]:
    print(f"{t:.5f}  {c:<16} = " + " + ".join(terms))
EOF
```

（向量路未配百炼 key 时用离线哈希兜底，名次与上表一致；配了真 embedding
向量路名次可能变化——那正是步骤 5 要观察的。）

## 步骤 5（动手 A，推荐）：接上真向量路

哈希兜底是词袋子，和 BM25 高度同质；要看语义召回的真实威力需配百炼：

1. 百炼控制台 `bailian.console.aliyun.com` → API-KEY → 创建（**和 DeepSeek
   key 一样，不要发给任何人/聊天**）；
2. 追加进 `.env`（与 DEEPSEEK_API_KEY 并存）：

   ```bash
   echo 'DASHSCOPE_API_KEY=sk-你的key' >> .env
   ```

3. 重跑 `uv run --env-file .env python -m risklist_kg_miner.rag`，
   首行应显示 `向量路: dashscope:text-embedding-v3`。
   对比离线版：查询"疾控中心新任主任是谁"的向量路排序会变化——
   text-embedding-v3 理解"新任主任"与"任命…主任"的语义关联。
   花费：40 块语料 + 3 个问题，约 2000 token，不到 1 分钱。

## 步骤 6（动手 B）：钉一条查询承诺

在 `tests/test_rag.py` 加一条：查询"吴国强的免职机构"（走离线路，不需要
任何 key），断言融合 top-5 里 `notice-004` 的吴国强行排在第一。跑绿后：

```bash
git add -A && git commit -m "test: 钉死吴国强免职的检索承诺"
git push
```

## 面试 hook

- "三路召回各自的价值与失效面？" → 举"疾控中心"例子：字面包含判断在
  图谱路失效、BM25 二元组命中、向量语义兜底——三路是失效面互补，不是冗余。
- "RRF 的 k=60 是什么？" → 名次平滑常数：k 越大，头部名次差距对分数影响
  越小，融合越"民主"；60 是标准经验值。
- "向量路挂了怎么办？" → 协议注入 + 异常软降级为空路，RRF 只少一路贡献，
  主链路照常出结果——和公司"图谱 60s 超时软降级"同一设计。
