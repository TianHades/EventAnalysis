#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""§4.0 spike: akshare 函数签名/异常/真实返回实跑。输出尽量精简。"""
import inspect, traceback, sys
sys.stdout.reconfigure(encoding='utf-8')

def run_with_timeout(fn, timeout=25):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FTimeout
    with ThreadPoolExecutor(max_workers=1) as ex:
        f = ex.submit(fn)
        try:
            return f.result(timeout=timeout)
        except FTimeout:
            return ("TIMEOUT", f"挂起>{timeout}s")
        except Exception as e:
            return ("EXC", f"{type(e).__name__}: {str(e)[:160]}")

def show(name, fn, sample_n=2):
    try:
        sig = str(inspect.signature(fn))
    except Exception as e:
        sig = f"<sig? {e}>"
    res = run_with_timeout(fn)
    if isinstance(res, tuple) and res and res[0] in ("EXC", "TIMEOUT"):
        print(f"\n### {name}\n  sig: {sig}\n  RESULT: {res[0]} {res[1]}")
        return
    df = res
    try:
        info = f"shape={df.shape} cols={list(df.columns)}"
        head = df.head(sample_n).to_string()
        print(f"\n### {name}\n  sig: {sig}\n  {info}\n{head[:600]}")
    except Exception as e:
        print(f"\n### {name}\n  sig: {sig}\n  RESULT(non-df): {str(res)[:300]}")

import akshare as ak

# 1. 基本信息与日历
show("stock_info_a_code_name", lambda: ak.stock_info_a_code_name())
show("tool_trade_date_hist_sina", lambda: ak.tool_trade_date_hist_sina())

# 2. 个股后复权日线（eastmoney 源，网络关键）
show("stock_zh_a_hist(600519 hfq)",
     lambda: ak.stock_zh_a_hist(symbol="600519", period="daily", start_date="20240101", end_date="20240110", adjust="hfq"))

# 3. 指数：价格 vs 全收益
show("index_zh_a_hist(sh000300 price)",
     lambda: ak.index_zh_a_hist(symbol="000300", period="daily", start_date="20240101", end_date="20240110"))
show("index_zh_a_hist(H00300 全收益)",
     lambda: ak.index_zh_a_hist(symbol="H00300", period="daily", start_date="20240101", end_date="20240110"))

# 4. 改名接口（重点：异常行为）
show("stock_info_sz_change_name('简称变更')",
     lambda: ak.stock_info_sz_change_name(symbol="简称变更"))
print("\n--- stock_info_change_name 真假代码对比 ---")
for code in ["000001", "600519", "999999", "000000"]:
    res = run_with_timeout(lambda c=code: ak.stock_info_change_name(symbol=c))
    if isinstance(res, tuple) and res[0] in ("EXC", "TIMEOUT"):
        print(f"  {code}: {res[0]} {res[1]}")
    else:
        print(f"  {code}: rows={len(res)} cols={list(res.columns) if hasattr(res,'columns') else res}")

# 5. 退市
show("stock_info_sh_delist", lambda: ak.stock_info_sh_delist())
show("stock_info_sz_delist('终止上市公司')",
     lambda: ak.stock_info_sz_delist(symbol="终止上市公司"))

# 6. 申万行业清单
show("sw_index_first_info", lambda: ak.sw_index_first_info())
show("sw_index_second_info", lambda: ak.sw_index_second_info())
show("sw_index_third_info", lambda: ak.sw_index_third_info())

# 7. 申万成分股：三级/二级/一级代码分别实测
print("\n--- index_component_sw 不同级别代码 ---")
for code in ["801001", "801010", "850111", "850111.SI"]:
    res = run_with_timeout(lambda c=code: ak.index_component_sw(symbol=c))
    if isinstance(res, tuple) and res[0] in ("EXC", "TIMEOUT"):
        print(f"  {code}: {res[0]} {res[1]}")
    else:
        print(f"  {code}: rows={len(res)} cols={list(res.columns) if hasattr(res,'columns') else res}")

# 8. 市值快照
show("stock_zh_a_spot_em", lambda: ak.stock_zh_a_spot_em())

print("\n=== SPIKE DONE ===")
