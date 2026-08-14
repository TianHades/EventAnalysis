"""Replay：一次完整复盘的运行记录（可追溯，spec §8）。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Replay(BaseModel):
    """单次复盘运行。记录三版本以支持可追溯（非数值可复现）。"""

    id: str
    event_id: str
    pipeline_version: str
    prompt_hash: str
    model_alias: str
    cli_version: str
    started_at: datetime
    finished_at: datetime | None = None
    status: str = Field(default="running", description="running/done/failed")
    node_outputs: dict[str, Any] = Field(default_factory=dict, description="节点产物 JSON 快照")
    report_path: str | None = None
    # 向前兼容 Phase 2 向量检索（Phase 1 留空）
    embedding_text: str | None = None
    embedding_model: str | None = None
