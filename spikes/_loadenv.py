"""共享：spike 脚本加载 .env（确保 EMBEDDING_BACKEND 等 env 一致）。

在每个 spike 顶部加：from _loadenv import *  # noqa: F401
"""
from dotenv import load_dotenv

load_dotenv(override=True)
