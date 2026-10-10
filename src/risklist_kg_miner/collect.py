"""v0.2 采集器：HTML -> 正文文本（去样板），供规则抽取使用。"""

from __future__ import annotations

from html.parser import HTMLParser

_SKIP_TAGS = {"head", "script", "style", "nav", "footer", "header"}
_BLOCK_TAGS = {"p", "tr", "div", "li", "h1", "h2", "h3", "h4", "br"}
_CELL_TAGS = {"td", "th"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif self._skip_depth == 0 and tag in _BLOCK_TAGS:
            self._chunks.append("\n")
        elif self._skip_depth == 0 and tag in _CELL_TAGS:
            self._chunks.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._chunks.append(data)


def html_to_text(html: str) -> str:
    extractor = _TextExtractor()
    extractor.feed(html)
    raw = "".join(extractor._chunks)
    lines = [" ".join(line.split()) for line in raw.splitlines()]
    return "\n".join(line for line in lines if line)


if __name__ == "__main__":
    import sys
    from pathlib import Path

    if len(sys.argv) != 2:
        raise SystemExit("用法: python -m risklist_kg_miner.collect <HTML路径>")
    text = html_to_text(Path(sys.argv[1]).read_text(encoding="utf-8"))
    for no, line in enumerate(text.splitlines(), start=1):
        print(f"L{no}: {line}")
