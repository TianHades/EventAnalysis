"""LLMNode 基类：封装 Reasoner 调用 + prompt/输出 schema 指纹。"""

from __future__ import annotations

import hashlib
import json
from abc import abstractmethod
from typing import Any

from pydantic import BaseModel

from ...llm import Reasoner
from ..node import Node


class LLMNode(Node):
    """推理节点基类。子类设置 output_model + system_prompt + build_user，可选 post_process。"""

    output_model: type[BaseModel] = BaseModel

    def __init__(self, reasoner: Reasoner, storage=None) -> None:
        super().__init__(storage)
        self.reasoner = reasoner

    @abstractmethod
    def system_prompt(self) -> str: ...

    @abstractmethod
    def build_user(self, inputs: dict[str, Any]) -> str: ...

    def post_process(self, llm_output: BaseModel, inputs: dict[str, Any]) -> Any:
        return llm_output

    def prompt_hash(self) -> str:
        schema = json.dumps(self.output_model.model_json_schema(), ensure_ascii=False, sort_keys=True)
        return hashlib.md5((self.system_prompt() + schema).encode()).hexdigest()

    def model_alias(self) -> str:
        return getattr(self.reasoner, "model_alias", "")

    def run(self, inputs: dict[str, Any]) -> Any:
        out = self.reasoner.call(
            system=self.system_prompt(),
            user=self.build_user(inputs),
            output_model=self.output_model,
        )
        return self.post_process(out, inputs)
