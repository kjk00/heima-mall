# 10000 人同时到达下单压测

测试时间：2026-11-17T06:03:32.194436+00:00。Python 3.15.0，macOS-15.7.7-arm64-arm-64bit-Mach-O。

每个场景使用独立临时数据库、10000 个已登录账号，每人购物车预放同一商品1件。放行前提交全部买家，事件统一释放。工作线程上限为 64，其余买家排队。成功下单后立即进行演示支付。

测量真实WSGI请求入口、认证、校验、事务及SQLite；不含TCP连接、反向代理、FastAPI、真实支付、浏览器或注册登录成本，不修改商城数据库。虚拟到达并发与实际处理并发分别报告。

下单及用户总延迟从统一放行开始计时，包含排队；实际处理延迟及支付延迟不含执行队列等待。409库存不足是预期业务结果，500/503/异常及支付失败属于非预期失败。通过仅表示服务无非预期失败且数据一致，响应时间另行评估。

| 库存 | 用户 | 处理并发峰值 | 下单成功 | 库存不足 | 非预期失败 | 支付成功 | 剩余库存 | 全部完成(s) | 下单P95含排队(s) | 下单P95处理(ms) | 一致性 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 10000 | 10000 | 64 | 9999 | 0 | 4 | 9996 | 1 | 37.9191 | 34.142 | 568.370 | 通过 |

## 库存 10000 件

放行前提交：10000；准备提交耗时：0.0293秒；每秒完成买家：263.72；应用错误日志：4条。

延迟分布（毫秒）：

```json
{
  "queue_ms": {
    "min_ms": 3.48,
    "mean_ms": 14294.966,
    "p50_ms": 12197.623,
    "p95_ms": 33916.755,
    "p99_ms": 36330.919,
    "max_ms": 36973.004
  },
  "checkout_service_ms": {
    "min_ms": 0.807,
    "mean_ms": 118.527,
    "p50_ms": 2.124,
    "p95_ms": 568.37,
    "p99_ms": 3050.641,
    "max_ms": 10333.707
  },
  "checkout_ms": {
    "min_ms": 36.891,
    "mean_ms": 14413.494,
    "p50_ms": 12256.5,
    "p95_ms": 34141.692,
    "p99_ms": 36597.439,
    "max_ms": 37903.933
  },
  "payment_ms": {
    "min_ms": 0.57,
    "mean_ms": 119.943,
    "p50_ms": 2.678,
    "p95_ms": 565.577,
    "p99_ms": 3017.264,
    "max_ms": 10394.113
  },
  "total_ms": {
    "min_ms": 41.207,
    "mean_ms": 14533.426,
    "p50_ms": 12416.689,
    "p95_ms": 34301.186,
    "p99_ms": 36728.395,
    "max_ms": 37918.775
  }
}
```

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

承载验收：失败；库存全部分配：否。

异常样本：

```json
{
  "launch_error": null,
  "requests": [
    {
      "user": 1611,
      "checkout_status": 503,
      "queue_ms": 2680.691374989692,
      "checkout_service_ms": 10333.707334008068,
      "checkout_ms": 13014.39870899776,
      "checkout_error": "数据库暂时繁忙，请稍后重试",
      "total_ms": 13014.39991701045
    },
    {
      "user": 6690,
      "checkout_status": 201,
      "queue_ms": 19039.07766699558,
      "checkout_service_ms": 1.9465000077616423,
      "checkout_ms": 19041.024167003343,
      "order_id": "HM20261117D4290215D1FC",
      "payment_ms": 10382.630666979821,
      "payment_status": 503,
      "payment_error": "数据库暂时繁忙，请稍后重试",
      "total_ms": 29423.656667000614
    },
    {
      "user": 6836,
      "checkout_status": 201,
      "queue_ms": 19687.558250006987,
      "checkout_service_ms": 1.8714169855229557,
      "checkout_ms": 19689.42966699251,
      "order_id": "HM202611174BCC9E1F110C",
      "payment_ms": 10394.11324998946,
      "payment_status": 503,
      "payment_error": "数据库暂时繁忙，请稍后重试",
      "total_ms": 30083.544624998467
    },
    {
      "user": 6965,
      "checkout_status": 201,
      "queue_ms": 20263.23691700236,
      "checkout_service_ms": 1.9572919991333038,
      "checkout_ms": 20265.194209001493,
      "order_id": "HM202611173527BF3C630C",
      "payment_ms": 10367.366042017238,
      "payment_status": 503,
      "payment_error": "数据库暂时繁忙，请稍后重试",
      "total_ms": 30632.56170900422
    }
  ]
}
```

## 复现命令

```bash
python3 scripts/stress_purchase_wave.py --users 10000 --workers 64 --stocks 10000 --output reports/stress-purchase-10000-workers64.json
```

逐用户请求结果见 `stress-purchase-10000-workers64.json`。
