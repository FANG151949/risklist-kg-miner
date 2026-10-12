from risklist_kg_miner.cli import main


def test_review_prints_risk_table_and_queue(capsys, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert main(["review"]) == 0
    out = capsys.readouterr().out
    assert "陈志远" in out
    assert "转人工复核" in out
    assert "人工复核队列" in out
    assert "高" in out  # 评分卡分级表在


def test_ingest_degrades_gracefully_without_graph(capsys, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:1")  # 端口无监听，连接立刻被拒
    assert main(["ingest"]) == 0
    out = capsys.readouterr().out
    assert "软降级" in out
    assert "合并 11 组" in out  # 图挂了也不拦合并与评分
    assert "图谱落库: 未落库" in out


def test_search_offline_returns_evidence(capsys, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:1")
    assert main(["search", "吴国强的免职机构"]) == 0
    out = capsys.readouterr().out
    assert "notice-004" in out  # 证据行所在公告进 top-8
    assert "图谱路降级" in out
