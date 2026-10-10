# 教程 03：v0.3 校验与合并（validator agent 内核）

## 需求（谁提的、原来怎么痛）

v0.2 验收时业务同事又说了两个痛点：

1. "同一个人在不同公告里**写法不一样**：有时机构写全称'河州市卫生健康委员会'，
   有时写简称'市卫生健康委员会'，Excel 里去重去不掉。"
2. "任免是会**变的**：上月发的任命，这月可能又免了。两张表一对，同一个人
   既是'任命'又是'免去'，以哪张为准？"

原来的做法：人工瞪眼比对，谁最新谁说了算——但公告多了根本瞪不过来。

**验收标准**：多来源记录进来 → 能合并的自动合并 → 矛盾的有明确裁决
→ 裁决不了的不猜，标记出来转人工复核。

## 核心业务事实：宁可漏报，不可误报

这是风控系统的铁律，也是面试高频考点。陈志远的任免冲突如果猜错了：

- 猜成"已免去"但他实际在职 → 漏掉一个该进名单的人（漏报）
- 猜成"在职"但他实际已免 → 把清白人写进高风险名单（**误报**）

误报比漏报严重：误报会冤枉真实的人/企业，有法律风险，还摧毁业务方对
系统的信任。所以规则定死：**拿不准就标记 conflict 转人工，绝不硬猜。**

## 步骤 1：跑合并器看现状

```bash
uv run python -m risklist_kg_miner.validate
```

预期输出：

```
[WARN] 陈志远 免去河州市发展和改革委员会副主任  <- LLM 不可用，取保守结论，转人工复核（来源：notice-001、notice-006）
[OK ] 林慧敏 任命河州市教育局副局长  <- 单源记录（来源：notice-001）
...
共 9 条原始记录 -> 8 条合并结论
```

两条信息：

- 9 条原始记录 → 8 条结论：陈志远在 notice-001 被任命、notice-006 被免去，
  两条合并成了一个结论。
- `[WARN]`：现在没配 LLM，系统没瞎猜，取了保守结论（免去优先）并标记
  转人工复核。**降级不是失败，是设计**——D3：LLM 不做单点故障。

## 步骤 2：三层裁决链（打开 src/risklist_kg_miner/validate.py）

合并按 (人, 职务) 分组，然后走三层：

```
第 1 层 规则：多源动作一致 + 机构是"全称/简称"关系（后缀匹配）
             → MERGED，取最长机构名，零 LLM 成本
第 2 层 LLM：动作矛盾（任命 vs 免去）→ 把候选清单发给 DeepSeek，
             让它输出 {"latest": 候选编号, "reason": "..."} 结构化裁决
第 3 层 降级：LLM 不可用/输出非法 → 保守结论（免去优先）+ CONFLICT 标记，
             转人工复核队列
```

对应公司 platform 的设计（network-agent-ts 精读笔记）：
**确定性问题规则先行，LLM 只做语义判断；冲突取更保守的一方。**
`LLMClient` 是一个 Protocol（接口），测试用 `FakeLLM` 塞 canned 回复，
生产用 `deepseek.py` 里的 `DeepSeekClient`——这正是 D3"LLM 可替换可降级"的结构。

## 步骤 3：测试钉死的业务承诺

```bash
uv run pytest tests/test_validate.py -v
```

| 测试 | 承诺 |
|---|---|
| test_single_record_is_confirmed | 单源记录原样确认 |
| test_org_alias_merges_to_full_name | 简称/全称自动合并取全称，不动用 LLM |
| test_conflict_without_llm_goes_to_review | 无 LLM 时冲突不猜，免去优先 + 转人工 |
| test_llm_adjudicates_conflict | LLM 裁决生效，且 prompt 里带候选人名来源 |
| test_llm_failure_falls_back_to_conservative | LLM 返回垃圾也不炸，保守降级 |
| test_org_matches_suffix_rule | 后缀匹配本身的行为 |

## 步骤 4（动手 A，推荐）：接上真 DeepSeek

1. key 在 DeepSeek 开放平台创建（`platform.deepseek.com` → API keys），
   形如 `sk-` 开头，按 token 计费。
2. **不要把 key 发给任何人/任何聊天**。保存方式二选一：
   - 持久化（推荐）：把 `export DEEPSEEK_API_KEY=sk-你的key` 追加到 `~/.zshrc`；
   - 或写入仓库根目录 `.env`（已在 `.gitignore`，永远不会被提交）：
     `echo 'DEEPSEEK_API_KEY=sk-你的key' > .env`
3. 跑一次真实裁决（只发送合成数据，DeepSeek 计费下本次调用约几分钱）。
   `.env` 方式用 `uv run --env-file .env` 把变量临时注入进程，不装任何 dotenv 包：

   ```bash
   uv run --env-file .env python -c "
   from risklist_kg_miner.extract import extract_appointments
   from risklist_kg_miner.collect import html_to_text
   from risklist_kg_miner.validate import merge
   from risklist_kg_miner.deepseek import default_client
   from pathlib import Path

   s = Path('data/samples')
   r1 = extract_appointments((s/'notice-001.txt').read_text(encoding='utf-8'), source='notice-001')
   r6 = extract_appointments(html_to_text((s/'notice-006.html').read_text(encoding='utf-8')), source='notice-006')
   chen = [r for r in r1 + r6 if r.person == '陈志远']
   m = merge(chen, llm=default_client())[0]
   print(m.status, m.record.action, '<-', m.note)
   "
   ```

   预期：`merged 免去 <- LLM 判定以notice-006为准：...`（理由由模型生成）。
   如果报错 `缺少 DEEPSEEK_API_KEY` 或网络超时——这正是降级路径存在的意义，
   demo 里的 `[WARN]` 分支就是为它准备的。

## 步骤 5（动手 B）：给冲突种子里一条测试

notice-006 已经放进 `data/samples/`，它制造了陈志远冲突。
在 `tests/test_validate.py` 里加一条测试：**用真实样本文件**（像
test_extract.py 那样从 `data/samples/` 读）证明"陈志远在 notice-001 任命、
notice-006 免去，merge 后只有一条结论且状态是 CONFLICT"。

跑绿后提交：

```bash
git add -A && git commit -m "test: 陈志远跨公告冲突用真实样本钉死"
git push
```

## 面试 hook

- "为什么 LLM 输出要 JSON Schema 约束？" → 防止自由文本里编证据，
  解析失败就降级，LLM 永远不拖死主链路。
- "规则与 LLM 冲突听谁的？" → 取更保守的一方，且全程留痕（sources/note），
  评估时能把每条冲突翻出来复盘。
