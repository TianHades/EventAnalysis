#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""spike: tushare pro 权威成分股源（概念 + 申万行业），验证可达 + 含中船特气。"""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv
load_dotenv()
import tushare as ts

token = os.getenv("TUSHARE_TOKEN")
if not token:
    print("无 TUSHARE_TOKEN"); sys.exit(1)
ts.set_token(token)
pro = ts.pro_api()

def norm(code):
    return str(code).split(".")[0].zfill(6)

# 1. 概念列表
print("=== pro.concept() 概念列表 ===")
try:
    concepts = pro.concept()
    print(f"[OK] 概念数: {len(concepts)} cols={list(concepts.columns)}")
    kws = ["电子特气","半导体材料","半导体","稀土","钨","锗","靶材","CMP","军工","永磁","材料","化学"]
    name_col = "name" if "name" in concepts.columns else concepts.columns[1]
    code_col = "code" if "code" in concepts.columns else concepts.columns[0]
    matched = concepts[concepts[name_col].str.contains("|".join(kws), na=False)]
    print(f"  匹配概念 {len(matched)} 个:")
    for _, r in list(matched.head(15).iterrows()):
        print(f"    {r[code_col]}  {r[name_col]}")
except Exception as e:
    print(f"[FAIL] concept: {type(e).__name__}: {str(e)[:120]}")
    concepts = None

# 2. 概念成分股（取电子特气/半导体材料等）
print("\n=== pro.concept_detail() 成分股 ===")
if concepts is not None:
    targets = ["688146"]  # 中船特气
    for _, r in list(matched.head(6).iterrows()):
        cid, cname = r[code_col], r[name_col]
        try:
            det = pro.concept_detail(id=cid)
            codes = [norm(c) for c in det["ts_code"]] if "ts_code" in det.columns else []
            has = [t for t in targets if t in codes]
            print(f"[OK] {cname}({cid}): {len(det)}只, 含中船特气={bool(has)}  样例={codes[:4]}")
        except Exception as e:
            print(f"[FAIL] {cname}({cid}): {type(e).__name__}: {str(e)[:70]}")

# 3. 申万行业成分股（权威，绕开 akshare 坏函数）
print("\n=== pro.index_classify + index_member (申万) ===")
try:
    ic = pro.index_classify(level="L3", src="SW2021")
    print(f"[OK] 申万三级指数: {len(ic)} cols={list(ic.columns)}")
    rel = ic[ic["industry_name"].str.contains("半导体材料|稀土|钨|化学|特气|军工|电子化学", na=False)]
    print("  相关三级:", list(zip(rel["index_code"].head(8), rel["industry_name"].head(8))))
    # 取第一个的成分股
    if not rel.empty:
        ic_code = rel.iloc[0]["index_code"]
        mem = pro.index_member(index_code=ic_code)
        codes = [norm(c) for c in mem["con_code"]] if "con_code" in mem.columns else []
        print(f"[OK] index_member({ic_code}): {len(mem)}只, 含688146={'688146' in codes}  样例={codes[:5]}")
except Exception as e:
    print(f"[FAIL] 申万: {type(e).__name__}: {str(e)[:120]}")
