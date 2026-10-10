# 教程 02：v0.2 采集器（HTML → 正文）

## 需求（谁提的、原来怎么痛）

v0.1 验收时业务同事说："公告是**网页**上的，不是 txt。而且每个网站的页面长得
完全不一样。"原来的做法是把网页文字全选复制到 Excel 再人工挑，导航栏、页脚、
统计脚本里的废话全跟着粘了进去。

**验收标准**：给一个 HTML 文件 → 输出干净的正文文本 → 版式 A 的页面能抽出记录。

## 三种版式（核心业务事实：来源异构）

| 文件 | 版式 | 任免行长什么样 |
|---|---|---|
| notice-002.html | A 段落式 | `任命 罗建军 为 江州市商务局副局长；` |
| notice-003.html | B 表格式 | `孙丽华 \| 免去 \| 河州市卫生健康委员会 \| 副调研员` |
| notice-004.html | C 叙述式 | `经研究，周晓梅同志任河州市交通运输局副局长，试用期一年。` |

## 步骤 1：跑采集器

```bash
uv run python -m risklist_kg_miner.collect data/samples/notice-002.html
```

预期输出（注意导航、页脚、script 全不见了）：

```
L1: 江州市人民政府任免通知
L2: 江政任〔2026〕7号
L3: 各县（区）人民政府，市政府各部门：
L4: 经研究决定——
L5: 任命 罗建军 为 江州市商务局副局长；
L6: 任命 唐雪 为 江州市审计局总审计师；
L7: 上述任免自 2026年8月20日 起生效。
L8: 特此通知。
L9: 江州市人民政府
L10: 2026年8月25日
```

打开 `src/risklist_kg_miner/collect.py` 看实现：用的是 Python 标准库
`html.parser`（零依赖），核心是"按标签决定行为"——`script/style/nav/footer/
header/head` 里的内容直接跳过，`p/tr/h1` 等块级标签换行，`td/th` 用 ` | ` 分隔。
这就是"确定性规则去样板"：不聪明，但快、可测、可解释。

## 步骤 2：端到端跑三种版式

```bash
uv run python -c "
from pathlib import Path
from risklist_kg_miner.collect import html_to_text
from risklist_kg_miner.extract import extract_appointments
for name in ['notice-002.html', 'notice-003.html', 'notice-004.html']:
    t = html_to_text(Path(f'data/samples/{name}').read_text(encoding='utf-8'))
    print(name, '->', len(extract_appointments(t)), '条')
"
```

预期输出：

```
notice-002.html -> 2 条
notice-003.html -> 0 条
notice-004.html -> 0 条
```

## 步骤 3：归纳痛点（v0.3 的立项依据）

观察两个事实：

1. **版式一变就瞎**：B、C 两种版式一条都抽不出来。如果按 v0.1 的思路给每种
   版式各写一条正则，规则会随来源数量无限膨胀，每加一个网站就要发版一次。
2. **同一事实多个说法**：notice-003 里孙丽华的机构写作"河州市卫生健康委员会"
   （全称），notice-001 里同一个人的同一职务写作"市卫生健康委员会"（简写）。
   两份来源合并时，规则判不了这两条是不是同一件事。

结论：纯规则覆盖不了异构来源和冲突合并 → v0.3 引入 validator agent：
**规则优先、LLM 兜底、冲突取更保守结论**。

## 步骤 4（动手，你自己完成）

1. 自己编第五种版式 `data/samples/notice-005.html`（比如口语式：
   "市委决定，冯建华不再担任市水利局局长职务。"），外壳参考 004。
2. 跑步骤 2 的命令，确认你的新版式也是 0 条——亲手把痛点再制造一次。
3. 在 `tests/test_collect.py` 加一个测试钉死这个现状：

```python
def test_your_layout_yields_nothing_yet():
    records = extract_appointments(_collect("notice-005.html"))
    assert records == []
```

4. `uv run pytest -q` 全绿后自己提交：

```bash
git add -A
git commit -m "feat: 样例补充第五种版式，钉死规则覆盖不了的现状"
git push
```
