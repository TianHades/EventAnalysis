"""adapters 共享网络工具：重试判定 + 超时包装。

- network_retry_predicate：tenacity retry= 判定函数。内建 ConnectionError 匹配不到
  akshare 内部 requests 抛的 requests.exceptions.ConnectionError（兄弟类，isinstance=False），
  需动态导入 requests.exceptions 一并命中。
- call_with_timeout：daemon 线程包装 akshare 调用，超时抛 TimeoutError
  （sina/pro 接口偶发网络挂起无库级超时，避免卡死整个 replay）。
"""

from __future__ import annotations

import threading


def _requests_exception_types() -> tuple[type[BaseException], ...]:
    """动态导入 requests.exceptions 网络异常类（requests 未装时返回空）。"""
    try:
        from requests import exceptions as req_exc
    except Exception:
        return ()
    return (req_exc.ConnectionError, req_exc.Timeout, req_exc.ChunkedEncodingError)


def network_retry_predicate(e: BaseException) -> bool:
    """可重试的瞬时网络异常判定（tenacity retry_if_exception 用）。

    命中：内建 ConnectionError / TimeoutError / OSError + requests.exceptions 的
    ConnectionError / Timeout / ChunkedEncodingError。其余（KeyError/ValueError 等数据
    问题）不重试。
    """
    if isinstance(e, (ConnectionError, TimeoutError, OSError)):
        return True
    return isinstance(e, _requests_exception_types())


def call_with_timeout(func, kwargs: dict, timeout: int = 60):
    """daemon 线程包装调用，超时抛 TimeoutError（daemon 线程不阻塞进程退出）。"""
    box: dict = {"result": None, "exc": None}

    def _run() -> None:
        try:
            box["result"] = func(**kwargs)
        except Exception as e:
            box["exc"] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        raise TimeoutError(f"{getattr(func, '__name__', 'akshare')} 超时({timeout}s)")
    if box["exc"]:
        raise box["exc"]
    return box["result"]
