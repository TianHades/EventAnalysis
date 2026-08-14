#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""spike: 同花顺概念板块（更权威的概念→标的源）是否可达 + 能否找到中船特气？"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import akshare as ak

# 1. 同花顺概念列表
print("=== 同花顺概念列表 ===")
concepts = None
for sym in ["概念", "概念资金流", "80"]:
    try:
        df = ak.stock_board_concept_name_ths(symbol=sym)
        print(f"[OK] stock_board_concept_name_ths(symbol={sym!r}): shape={df.shape} cols={list(df.columns)}")
        concepts = df
        break
    except Exception as e:
        print(f"[FAIL] symbol={sym!r}: {type(e).__name__}: {str(e)[:80]}")

# 2. 直接取概念成分股
print("\n=== 同花顺概念成分股 ===")
targets = ["电子特气", "半导体材料", "半导体概念", "稀土永磁", "军工", "中船系"]
found_688146 = []
for name in targets:
    try:
        df = ak.stock_board_concept_cons_ths(symbol=name)
        code_col = "代码" if "代码" in df.columns else df.columns[0]
        codes = df[code_col].astype(str).str.zfill(6).tolist()
        has = "688146" in codes
        print(f"[OK] {name}: {len(df)}只, 含688146(中船特气)={has}  样例={codes[:3]}")
        if has: found_688146.append(name)
    except Exception as e:
        print(f"[FAIL] {name}: {type(e).__name__}: {str(e)[:70]}")

print(f"\n688146(中船特气) 出现在同花顺概念: {found_688146}")
