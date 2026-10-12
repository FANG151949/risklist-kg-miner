# 教程 04：v0.4 图谱沉淀（Neo4j：事件流水 + 任职状态双写）

## 需求（谁提的、原来怎么痛）

v0.3 验收时业务同事问："河州市发改委历任副主任都有谁？陈志远现在到底
在不在任？孙丽华这个人的全部任免经过翻给我看看。"

原来的做法：Excel 按机构筛选——但"这个人**历任**过什么职务"要跨多张表
vlookup；"**现任还是已免**"要人工看公告日期。数据量一上来根本查不动。

**验收标准**：合并结论落 Neo4j，能回答两类查询——人的任免时间线、
机构某职务的历史任职名单（多跳关系）。

## 核心业务事实：事件流水 vs 当前状态

图里存两类东西，这是本版最重要的建模决策：

| 存什么 | 对应节点/关系 | 回答什么问题 | 特性 |
|---|---|---|---|
| 每条抽取记录 | `(:Event)` + 关系 | "陈志远经历过什么？" | 不可变流水，冲突双方（任命+免去）都留痕，审计可回放 |
| 合并结论 | `(Person)-[:STAFF]->(Org)` | "陈志远现在是什么状态？" | 可更新，status 保留 conflict 待人工复核 |

风控审计的铁律是**留痕**：合并层哪怕取了保守结论，原始事件一条不丢——
事后复核人能回放"当时系统看到了什么"。

另外两个建模要点：

- **event_id = 来源#L行号**：天然唯一，还能反查证据行（呼应 v0.1 的行号承诺）；
- **全部用 MERGE 不用 CREATE**：同一批公告重跑 N 次，图里不会产生重复节点
  （测试 `test_ops_are_idempotent_merges_only` 钉死这一点）。

## 步骤 1：起 Neo4j 并落库

本机 Neo4j 5.26 社区版已装在 `~/tools/neo4j-community-5.26.0`（用系统 Java 17
直接跑；不用 Docker 是因为本机 Docker Desktop 版本过旧，容器里 JVM 建线程会
`pthread_create failed (EPERM)`）。

```bash
~/tools/neo4j-community-5.26.0/bin/neo4j status   # 查看状态（已在跑会显示 pid）
~/tools/neo4j-community-5.26.0/bin/neo4j start    # 未运行时启动；stop 停止

# 跑落库 demo：抽取 -> 合并 -> 建图 -> 三个多跳查询
uv run python -m risklist_kg_miner.neo4j_store
```

其他机器上更常见的起法是 Docker：`docker run -d --name risklist-neo4j --rm
-p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/password neo4j:5-community`。

预期输出（已在本机实测）：

```
落库 17 条操作

== 陈志远任免时间线（事件流水，冲突双方留痕） ==
  2026-09-01 任命 河州市发展和改革委员会副主任
   日期待核 免去 河州市发展和改革委员会副主任

== 河州市发改委副主任历史（多跳：人->事件->机构） ==
  陈志远 任命（2026-09-01）
  陈志远 免去（日期待核）

== 河州市发改委当前任职状态（合并结论，conflict 即待人工复核） ==
  陈志远 副主任 [conflict] LLM 不可用，取保守结论，转人工复核
```

"日期待核"不是 bug：notice-006 里本来就没写"自 X 起生效"，图里如实留空
（`ORDER BY size(e.date) = 0, date` 让有日期的排前面）。

第三条最有信息量：`[conflict]` 直接写在图上——业务方一眼看到"这个人的
状态没定，别拿它做拒绝结论"，这正是"宁可漏报不误报"在图里的样子。

## 步骤 1b：Browser 里亲手跑（查询语句直接粘贴版）

打开 `http://localhost:7474`（用户名 neo4j，密码 password），把下面四条
逐条粘进顶部的 Cypher 输入框执行。注意 Browser 里用不了 `$参数`，所以
参数已写成字面量：

```cypher
// ① 全图概览：直接看到节点图（Person/Org/Event/Notice 四种颜色）
MATCH (n)-[r]-(m)
RETURN n, r, m;
```

```cypher
// ② 陈志远任免时间线
MATCH (p:Person {name: '陈志远'})-[:SUBJECT]->(e)-[:AT]->(o)
RETURN e.date AS date, e.action AS action, o.name AS org, e.position AS position
ORDER BY size(e.date) = 0, date;
```

```cypher
// ③ 发改委副主任历史（人->事件->机构 两跳）
MATCH (p:Person)-[:SUBJECT]->(e {position: '副主任'})-[:AT]->(o:Org {name: '河州市发展和改革委员会'})
RETURN p.name AS person, e.action AS action, e.date AS date
ORDER BY size(e.date) = 0, date;
```

```cypher
// ④ 空结果验证：卫健委 STAFF 应为 0 行——孙丽华机构是简称，
//    图里"市卫生健康委员会"是另一个独立 Org 节点（v0.5 归一化解决）
MATCH (p:Person)-[r:STAFF]->(o:Org {name: '河州市卫生健康委员会'})
RETURN p.name, r.position;
```

预期：①出节点图；②两行（任命 2026-09-01 + 免去 日期待核）；③两行陈志远；
④"No changes, no records"（0 行）。

另外验证幂等：再跑一次
`uv run python -m risklist_kg_miner.neo4j_store`，然后把 ①再执行一遍，
节点数不变（全 MERGE 落库）。

浏览器打开 `http://localhost:7474`（用户名 neo4j，密码 password），
把上面查询粘进 Browser 亲手跑一遍，能看到节点图。

## 步骤 2：代码分层（打开 src/risklist_kg_miner/graph_model.py 与 neo4j_store.py）

```
graph_model.py   纯函数：记录/结论 -> Cypher 操作（不碰数据库，离线可测）
neo4j_store.py   薄封装：执行 ops + 三个示例查询（连库才需要 neo4j 驱动）
```

这是 D2"归一化中间表示"的图版：**建模逻辑与数据库解耦**，换库只动 store 层。
测试全部打在建模层（tests/test_graph_model.py，5 条），CI 不需要数据库。

## 步骤 3：测试钉死的业务承诺

| 测试 | 承诺 |
|---|---|
| test_record_to_op_captures_event_fields | event_id=来源#L行号，字段完整含生效日期 |
| test_event_id_differs_across_sources | 跨来源同人不撞 event_id |
| test_ops_are_idempotent_merges_only | 只 MERGE 无 CREATE，重跑不重复 |
| test_real_samples_end_to_end_counts | 真实样本：7 事件 op + 6 结论 op，陈志远 conflict |
| test_empty_input_yields_empty_ops | 空输入安全 |

## 步骤 4（动手）：新增一条公告，看 op 数变化

仿照 notice-005，造 `data/samples/notice-007.html`（合成、声明虚构），
段落版式、写一条**任命**记录（新人新机构，比如"钱志斌任河州市大数据局
局长"）。然后在 tests/test_graph_model.py 加测试：

- 把 notice-007 纳入 build_ops 输入后，事件 op 数 = 8、结论 op 数 = 7；
- 图里应能查到钱志斌的任命事件（用 op 参数断言即可，不用真连库）。

跑绿后：

```bash
git add -A && git commit -m "feat: 样例补充第七种版式，钉死图谱 op 增长"
git push
```

## 面试 hook

- "图数据库比关系型强在哪？" → 多跳关系是**一等公民**：人->事件->机构
  两跳查询在 Neo4j 里是模式匹配，在 MySQL 里要两次 JOIN + 中间表；
  而且 schema 演进自由（加节点类型不动存量数据）。
- "为什么事件和状态分开存？" → 审计留痕与查询效率的权衡：流水只增不改
  （可回放），状态随合并结论更新（查询快）。
- "MERGE 和 CREATE 的区别？" → 分布式采集/重跑场景下幂等落库的关键，
  CREATE 会造重复，MERGE 按 key 存在即复用。
