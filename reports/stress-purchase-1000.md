# 1000 人同时购买压力测试

测试时间：2026-11-17T04:57:59.088135+00:00。Python 3.15.0，macOS-15.7.7-arm64-arm-64bit-Mach-O，8 核。

每个场景使用全新的临时 SQLite 数据库；1000 个独立账号已登录，每人购物车预放 1 件同一商品。所有线程在屏障处等待，一起释放，成功下单后立即进行演示支付。测试使用真实 WSGI 请求入口，保留请求校验、会话认证和业务事务；不包含 TCP、反向代理、前端或注册登录阶段，不修改运行中的商城数据库。

| 初始库存 | 参与用户 | 下单成功 | 库存不足 | 下单服务异常 | 支付成功 | 剩余库存 | 全部完成（秒） | 下单 P95（毫秒） | 一致性 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1000 | 1000 | 1000 | 0 | 0 | 1000 | 0 | 14.5322 | 12624.62 | 通过 |
| 100 | 1000 | 100 | 900 | 0 | 100 | 0 | 6.828 | 5257.389 | 通过 |
| 1 | 1000 | 1 | 999 | 0 | 1 | 0 | 10.8075 | 9843.363 | 通过 |

库存不足（409）是预期业务结果；500、503、异常或支付失败算作服务失败。库存一致性与服务承载能力分别判断。

## 库存 1000 件

同步放行：完成；观测到的峰值并行请求：1000；非预期失败：0。

下单延迟：{"min_ms": 5265.026, "mean_ms": 9661.85, "p50_ms": 9316.017, "p95_ms": 12624.62, "p99_ms": 13784.48, "max_ms": 14477.784}。

每个用户下单到支付完成的延迟：{"min_ms": 5270.429, "mean_ms": 9678.513, "p50_ms": 9344.716, "p95_ms": 12630.13, "p99_ms": 13791.979, "max_ms": 14481.095}。

同步放行后的实际请求启动偏移：{"min_ms": 4.578, "mean_ms": 476.373, "p50_ms": 250.469, "p95_ms": 1647.861, "p99_ms": 3052.703, "max_ms": 4896.558}。

- 通过：all_buyers_completed
- 通过：inventory_never_negative
- 通过：no_overselling
- 通过：inventory_matches_reserved_units
- 通过：orders_match_successful_responses
- 通过：one_order_per_buyer
- 通过：one_unit_per_order
- 通过：payment_responses_match_paid_orders
- 通过：sales_match_paid_orders
- 通过：remaining_carts_match_failed_checkouts
- 通过：prices_match_snapshot
- 通过：no_foreign_key_errors
- 通过：database_integrity
- 通过：available_inventory_fully_reserved

## 库存 100 件

同步放行：完成；观测到的峰值并行请求：1000；非预期失败：0。

下单延迟：{"min_ms": 61.559, "mean_ms": 3139.607, "p50_ms": 3233.098, "p95_ms": 5257.389, "p99_ms": 6378.471, "max_ms": 6780.907}。

每个用户下单到支付完成的延迟：{"min_ms": 62.624, "mean_ms": 3151.964, "p50_ms": 3240.116, "p95_ms": 5257.393, "p99_ms": 6378.474, "max_ms": 6780.912}。

同步放行后的实际请求启动偏移：{"min_ms": 4.195, "mean_ms": 724.643, "p50_ms": 273.116, "p95_ms": 2857.431, "p99_ms": 2863.683, "max_ms": 2866.345}。

- 通过：all_buyers_completed
- 通过：inventory_never_negative
- 通过：no_overselling
- 通过：inventory_matches_reserved_units
- 通过：orders_match_successful_responses
- 通过：one_order_per_buyer
- 通过：one_unit_per_order
- 通过：payment_responses_match_paid_orders
- 通过：sales_match_paid_orders
- 通过：remaining_carts_match_failed_checkouts
- 通过：prices_match_snapshot
- 通过：no_foreign_key_errors
- 通过：database_integrity
- 通过：available_inventory_fully_reserved

## 库存 1 件

同步放行：完成；观测到的峰值并行请求：1000；非预期失败：0。

下单延迟：{"min_ms": 3724.966, "mean_ms": 8004.549, "p50_ms": 7838.886, "p95_ms": 9843.363, "p99_ms": 10395.849, "max_ms": 10704.316}。

每个用户下单到支付完成的延迟：{"min_ms": 3724.967, "mean_ms": 8004.702, "p50_ms": 7838.889, "p95_ms": 9843.366, "p99_ms": 10395.851, "max_ms": 10704.32}。

同步放行后的实际请求启动偏移：{"min_ms": 3.759, "mean_ms": 489.387, "p50_ms": 276.076, "p95_ms": 1609.081, "p99_ms": 2835.37, "max_ms": 4582.264}。

- 通过：all_buyers_completed
- 通过：inventory_never_negative
- 通过：no_overselling
- 通过：inventory_matches_reserved_units
- 通过：orders_match_successful_responses
- 通过：one_order_per_buyer
- 通过：one_unit_per_order
- 通过：payment_responses_match_paid_orders
- 通过：sales_match_paid_orders
- 通过：remaining_carts_match_failed_checkouts
- 通过：prices_match_snapshot
- 通过：no_foreign_key_errors
- 通过：database_integrity
- 通过：available_inventory_fully_reserved

## 复现命令

```bash
python3 scripts/stress_purchase.py --users 1000 --stocks 1000 100 1 --output reports/stress-purchase.json
```

逐用户请求结果见 `stress-purchase-1000.json`。
