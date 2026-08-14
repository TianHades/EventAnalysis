"""ThemeIndex：主题→标的 索引（curated，治 LLM 提议召回遗漏）。

CandidateGen 在 LLM 提议之外，按产业链节点文本命中主题 keywords → 补入该主题标的，
经 StockMaster 反查真实性。保证中船特气这类"LLM 没想起但相关"的标的不被漏。
"""

from __future__ import annotations

from pathlib import Path


class ThemeIndex:
    def __init__(self, path: str | Path = "data/themes.yaml") -> None:
        self.themes: list[dict] = self._load(path)

    def _load(self, path: str | Path) -> list[dict]:
        p = Path(path)
        if not p.exists():
            return []
        try:
            import yaml

            data = yaml.safe_load(p.read_text(encoding="utf-8"))
            return (data or {}).get("themes", [])
        except Exception:
            return []

    def lookup(self, text: str) -> list[dict]:
        """文本命中主题 keywords 或主题名 → 返回 [{code, theme}]。"""
        if not text:
            return []
        out: list[dict] = []
        for th in self.themes:
            kws = th.get("keywords", [])
            hit = th["name"] in text or any(kw in text for kw in kws)
            if hit:
                for code in th.get("stocks", []):
                    out.append({"code": str(code), "theme": th["name"]})
        return out

    def all_codes(self) -> set[str]:
        return {str(c) for th in self.themes for c in th.get("stocks", [])}
