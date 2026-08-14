#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""spike: embedding 生成可行性（GLM 网关端点 + 本地 sentence-transformers）。"""
import os, sys, json, urllib.request
sys.stdout.reconfigure(encoding='utf-8')
tok = os.getenv('ANTHROPIC_AUTH_TOKEN') or os.getenv('ANTHROPIC_API_KEY')

print("=== 1. GLM/Zhipu 网关 embedding (embedding-3) ===")
url = "https://open.bigmodel.cn/api/paas/v4/embeddings"
for text in ["双减政策对K12教培的影响", "ChatGPT引爆AI算力行情"]:
    body = json.dumps({"model": "embedding-3", "input": text}).encode()
    req = urllib.request.Request(url, data=body, headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
            emb = data.get("data", [{}])[0].get("embedding", [])
            print(f"  '{text[:16]}' -> dim={len(emb)} ok")
            gateway_ok = True
    except Exception as e:
        print(f"  '{text[:16]}' FAIL {type(e).__name__}: {str(e)[:160]}")
        gateway_ok = False
        break

if gateway_ok:
    # 相似度验证：双减 vs ChatGPT 应有合理距离
    print("  (网关 embedding 可用)")

print("\n=== 2. sentence-transformers 本地 ===")
try:
    from sentence_transformers import SentenceTransformer
    print("  已装；尝试加载 bge-small-zh-v1.5（小模型，~100MB）...")
    m = SentenceTransformer("BAAI/bge-small-zh-v1.5")
    v = m.encode(["测试句子"])
    print(f"  本地模型 OK dim={len(v[0])}")
except ImportError:
    print("  sentence-transformers 未装（pip 安装需单独测，可能因网络/HF 受限）")
except Exception as e:
    print(f"  本地模型 FAIL {type(e).__name__}: {str(e)[:160]}（可能 HF 下载受限）")
