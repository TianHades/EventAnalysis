#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""spike followup: 个股日线替代源 + 成分股 KeyError 真因 + 重试"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import akshare as ak

def trycall(name, fn):
    try:
        df = fn()
        try:
            print(f"### {name}\n  shape={df.shape} cols={list(df.columns)}\n{df.head(2).to_string()[:300]}")
        except Exception:
            print(f"### {name}\n  raw type={type(df)} val={str(df)[:200]}")
    except Exception as e:
        print(f"### {name}\n  EXC {type(e).__name__}: {str(e)[:180]}")

# 1. 个股日线替代源（sina）—— eastmoney 被挡，试 sina
trycall("stock_zh_a_daily(sh600519 hfq, sina 源)",
        lambda: ak.stock_zh_a_daily(symbol="sh600519", start_date="20240101", end_date="20240110", adjust="hfq"))
# 2. 指数替代源（sina）
trycall("stock_zh_index_daily(sh000300, sina)",
        lambda: ak.stock_zh_index_daily(symbol="sh000300"))
trycall("stock_zh_index_daily(H00300 全收益)",
        lambda: ak.stock_zh_index_daily(symbol="H00300.CSI"))

# 3. 成分股 KeyError 真因——打印 二级/三级 原始返回
print("\n--- index_component_sw 二级原始返回 ---")
for code in ["801010", "801010.SI"]:
    try:
        df = ak.index_component_sw(symbol=code)
        print(f"  {code}: type={type(df)} shape={getattr(df,'shape',None)} cols={list(df.columns) if hasattr(df,'columns') else 'N/A'}")
        print(f"    content head:\n{str(df.head(3))[:400]}")
    except Exception as e:
        print(f"  {code}: EXC {type(e).__name__}: {str(e)[:200]}")

# 4. 个股基本信息替代（stock_info_a_code_name 失败，试 stock_zh_a_spot_em 的非 em 版 / tushare 免登录）
trycall("stock_info_a_code_name (重试)",
        lambda: ak.stock_info_a_code_name())

# 5. baostock 复测
print("\n--- baostock 复测 ---")
try:
    import baostock as bs
    lg = bs.login()
    print(f"  login: {lg.error_code} {lg.error_msg}")
    if lg.error_code == '0':
        rs = bs.query_history_k_data_plus("sh.600519", "date,code,close,volume,adjustflag",
                                           start_date="2024-01-02", end_date="2024-01-08", adjustflag="2")
        print(f"  query: {rs.error_code} {rs.error_msg}")
        rows=[]
        while rs.error_code=='0' and rs.next() and len(rows)<3:
            rows.append(rs.get_row_data())
        print(f"  rows: {rows}")
        bs.logout()
except Exception as e:
    print(f"  EXC {type(e).__name__}: {e}")
print("=== DONE ===")
