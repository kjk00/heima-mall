# 1000 人同时购买压力测试

测试时间：2026-11-17T05:55:47.851386+00:00。Python 3.15.0，macOS-15.7.7-arm64-arm-64bit-Mach-O，8 核。

每个场景使用全新的临时 SQLite 数据库；1000 个独立账号已登录，每人购物车预放 1 件同一商品。所有线程在屏障处等待，一起释放，成功下单后立即进行演示支付。测试使用真实 WSGI 请求入口，保留请求校验、会话认证和业务事务；不包含 TCP、反向代理、前端或注册登录阶段，不修改运行中的商城数据库。

| 初始库存 | 参与用户 | 下单成功 | 库存不足 | 下单服务异常 | 支付成功 | 剩余库存 | 全部完成（秒） | 下单 P95（毫秒） | 一致性 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1000 | 1000 | 1000 | 0 | 0 | 1000 | 0 | 5.1247 | 3636.008 | 通过 |
| 100 | 1000 | 100 | 900 | 0 | 100 | 0 | 2.9882 | 1844.094 | 通过 |
| 1 | 1000 | 1 | 999 | 0 | 1 | 0 | 5.4469 | 4738.098 | 通过 |

库存不足（409）是预期业务结果；500、503、异常或支付失败算作服务失败。库存一致性与服务承载能力分别判断。

## 库存 1000 件

同步放行：完成；观测到的峰值并行请求：1000；非预期失败：0。

下单延迟：{"min_ms": 38.965, "mean_ms": 1762.711, "p50_ms": 1623.743, "p95_ms": 3636.008, "p99_ms": 4309.679, "max_ms": 4962.361}。

每个用户下单到支付完成的延迟：{"min_ms": 48.537, "mean_ms": 1800.796, "p50_ms": 1662.446, "p95_ms": 3658.063, "p99_ms": 4310.566, "max_ms": 4963.409}。

同步放行后的实际请求启动偏移：{"min_ms": 4.397, "mean_ms": 478.32, "p50_ms": 303.768, "p95_ms": 954.059, "p99_ms": 959.908, "max_ms": 961.577}。

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

下单延迟：{"min_ms": 23.054, "mean_ms": 912.694, "p50_ms": 812.867, "p95_ms": 1844.094, "p99_ms": 2169.222, "max_ms": 2699.769}。

每个用户下单到支付完成的延迟：{"min_ms": 28.054, "mean_ms": 928.794, "p50_ms": 817.916, "p95_ms": 1844.094, "p99_ms": 2169.223, "max_ms": 2699.771}。

同步放行后的实际请求启动偏移：{"min_ms": 100.279, "mean_ms": 335.905, "p50_ms": 334.275, "p95_ms": 552.341, "p99_ms": 570.34, "max_ms": 574.069}。

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

下单延迟：{"min_ms": 635.0, "mean_ms": 3810.014, "p50_ms": 3796.588, "p95_ms": 4738.098, "p99_ms": 5002.147, "max_ms": 5232.29}。

每个用户下单到支付完成的延迟：{"min_ms": 635.0, "mean_ms": 3810.032, "p50_ms": 3796.588, "p95_ms": 4738.099, "p99_ms": 5002.148, "max_ms": 5232.291}。

同步放行后的实际请求启动偏移：{"min_ms": 4.854, "mean_ms": 362.227, "p50_ms": 175.576, "p95_ms": 1298.451, "p99_ms": 1871.353, "max_ms": 3571.483}。

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

逐用户请求结果见 `enterprise-stress-2026-11-17.json`。
