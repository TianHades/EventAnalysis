"""节点基类与缓存（spec §8）。

缓存键 = node_id + node_code_hash + inputs_hash + prompt_hash + model_alias + cli_version + cli_flags_hash
（改代码/flag/model 触发重算；推理→推理下游因 LLM 非确定性命中率被压制，断点续跑仍可用）。
"""

from __future__ import annotations

import hashlib
import math
import sys
from abc import ABC, abstractmethod
from typing import Any

# 哨兵：缓存 payload 重建（output_type 校验）失败 → 视为缓存 miss，走重算路径。
# 不能用 None（None 是合法的 miss 语义），用模块级唯一 object 区分"坏缓存"。
_CACHE_MISS = object()


def _hash_json(obj: Any) -> str:
    import json

    return hashlib.md5(json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


class Node(ABC):
    """节点基类。子类实现 run() 与 cache_key()。"""

    node_id: str = "node"
    output_type: Any = None  # 子类声明输出 Pydantic 类型（SQLite 缓存重建用）

    def __init__(self, storage=None) -> None:
        self.storage = storage
        self._mem: dict[str, Any] = {}  # 进程内缓存（处理 Pydantic 等非 JSON 对象）

    @abstractmethod
    def run(self, inputs: dict[str, Any]) -> Any:
        """执行节点，返回结构化输出（可 JSON 序列化）。失败按 spec §3.2 降级。"""

    def inputs_for_cache(self, inputs: dict[str, Any]) -> Any:
        """参与缓存键的输入指纹（默认整个 inputs）。"""
        return inputs

    def cache_key(self, inputs: dict[str, Any]) -> str:
        parts = [
            self.node_id,
            self._code_hash(),
            _hash_json(self.inputs_for_cache(inputs)),
            self.prompt_hash(),
            self.model_alias(),
            sys.version,
        ]
        return _hash_json(parts)

    def prompt_hash(self) -> str:
        """prompt 指纹（LLMNode 覆写：system_prompt + 输出 schema）。"""
        return ""

    def model_alias(self) -> str:
        """模型别名（LLMNode 覆写）。"""
        return ""

    def _code_hash(self) -> str:
        # 节点类及其基类所在 eventreplay 模块的源码 hash。
        # 必须覆盖整个模块：量化核心（如 measure_stock）与模块级常量（如估计窗宽度）
        # 定义在类外，只哈希类源码会让这些改动不触发缓存重算。
        import inspect

        parts: list[str] = []
        seen: set[str] = set()
        for klass in type(self).__mro__:
            mod = inspect.getmodule(klass)
            if mod is None or not mod.__name__.startswith("eventreplay"):
                continue
            if mod.__name__ in seen:
                continue
            seen.add(mod.__name__)
            try:
                parts.append(inspect.getsource(mod))
            except (OSError, TypeError):
                parts.append(klass.__name__)
        return hashlib.md5("\n".join(parts).encode()).hexdigest()

    def run_cached(self, inputs: dict[str, Any]) -> Any:
        """带缓存的执行：进程内缓存 + SQLite 持久缓存（读写均支持，按 output_type 重建）。"""
        key = self.cache_key(inputs)
        if key in self._mem:
            return self._mem[key]
        # 读 SQLite 持久缓存
        if self.storage is not None:
            payload = self.storage.get_cached_node(key)
            if payload is not None:
                output = self._from_jsonable(payload)
                if output is not _CACHE_MISS:
                    self._mem[key] = output
                    return output
                # 重建失败（schema 演进/坏缓存）视为 miss：走重算并覆盖写入（自愈）
        output = self.run(inputs)
        self._mem[key] = output
        if self.storage is not None:
            try:
                payload = self._to_jsonable(output)
                self.storage.save_node_run(
                    replay_id=str(inputs.get("_replay_id", "adhoc")),
                    node_id=self.node_id,
                    cache_key=key,
                    status="done",
                    payload=payload,
                )
            except Exception:
                pass
        return output

    @staticmethod
    def _to_jsonable(output: Any) -> Any:
        """输出 → 可标准 JSON 序列化的 payload；float NaN/inf 递归转 None。

        NaN/Inf 字面量非标准 JSON（json.loads 能吃但跨语言消费方/严格解析器会炸），
        model_dump 路径同样产出 NaN 字面量，故 dump 后再递归清洗一遍。
        """
        if output is None or isinstance(output, (str, int, bool)):
            return output
        if isinstance(output, float):
            return None if (math.isnan(output) or math.isinf(output)) else output
        if isinstance(output, dict):
            return {k: Node._to_jsonable(v) for k, v in output.items()}
        if isinstance(output, (list, tuple)):
            return [Node._to_jsonable(o) for o in output]
        if hasattr(output, "model_dump"):
            return Node._to_jsonable(output.model_dump(mode="json"))
        return str(output)

    def _from_jsonable(self, payload: Any) -> Any:
        """从 JSON payload 重建输出（声明 output_type 时按类型重建，否则原样返回）。

        重建失败返回 _CACHE_MISS 哨兵（run_cached 据此走重算覆盖，而非静默
        降级成 dict 让下游节点拿到错误类型）。
        """
        if self.output_type is None:
            return payload
        try:
            from pydantic import TypeAdapter
            return TypeAdapter(self.output_type).validate_python(payload)
        except Exception:
            return _CACHE_MISS
