#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""spike: eastmoney 概念板块成分股是否可达？能否找到中船特气(688146)？"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import akshare as ak

# 1. 概念列表，找半导体/特气/稀土/军工/钨/锗相关
concepts = ak.stock_board_concept_name_em()
print(f"概念板块共 {len(concepts)} 个")
names = concepts["板块名称"].tolist()
kw = ["电子特气","半导体材料","半导体","稀土","军工","钨","锗","靶材","CMP","永磁","材料"]
matched = [n for n in names if any(k in n for k in kw)]
print("相关概念:", matched[:25])

# 2. 取几个相关概念的成分股，验证可达 + 是否含 688146
def cons(name):
    try:
        df = ak.stock_board_concept_cons_em(symbol=name)
        return df
    except Exception as e:
        print(f"  [FAIL] {name}: {type(e).__name__}: {str(e)[:80]}")
        return None

target = "688146"
found_in = []
for c in matched[:8]:
    df = cons(c)
    if df is None: continue
    codes = df["代码"].astype(str).str.zfill(6).tolist() if "代码" in df.columns else []
    has = target in codes
    print(f"[{'OK' if df is not None else 'FAIL'}] {c}: 成分股 {len(df)} 只, 含688146={has}")
    if has: found_in.append(c)
    if df is not None and len(df)>0:
        print(f"    列={list(df.columns)[:5]} 样例={codes[:3]}")

print(f"\n688146(中船特气) 出现在概念: {found_in}")
