# 10000 人同时到达下单压测

测试时间：2026-11-17T06:00:06.979680+00:00。Python 3.15.0，macOS-15.7.7-arm64-arm-64bit-Mach-O。

每个场景使用独立临时数据库、10000 个已登录账号，每人购物车预放同一商品1件。放行前提交全部买家，事件统一释放。工作线程上限为 256，其余买家排队。成功下单后立即进行演示支付。

测量真实WSGI请求入口、认证、校验、事务及SQLite；不含TCP连接、反向代理、FastAPI、真实支付、浏览器或注册登录成本，不修改商城数据库。虚拟到达并发与实际处理并发分别报告。

下单及用户总延迟从统一放行开始计时，包含排队；实际处理延迟及支付延迟不含执行队列等待。409库存不足是预期业务结果，500/503/异常及支付失败属于非预期失败。通过仅表示服务无非预期失败且数据一致，响应时间另行评估。

| 库存 | 用户 | 处理并发峰值 | 下单成功 | 库存不足 | 非预期失败 | 支付成功 | 剩余库存 | 全部完成(s) | 下单P95含排队(s) | 下单P95处理(ms) | 一致性 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 10000 | 10000 | 256 | 9930 | 0 | 130 | 9870 | 70 | 41.2909 | 36.222 | 4046.057 | 通过 |
| 100 | 10000 | 256 | 100 | 9900 | 0 | 100 | 0 | 5.5132 | 4.171 | 572.908 | 通过 |
| 1 | 10000 | 256 | 1 | 9999 | 0 | 1 | 0 | 5.4146 | 4.001 | 566.048 | 通过 |

## 库存 10000 件

放行前提交：10000；准备提交耗时：0.034秒；每秒完成买家：242.18；应用错误日志：130条。

延迟分布（毫秒）：

```json
{
  "queue_ms": {
    "min_ms": 4.072,
    "mean_ms": 14824.122,
    "p50_ms": 12687.233,
    "p95_ms": 35261.835,
    "p99_ms": 37687.663,
    "max_ms": 38366.4
  },
  "checkout_service_ms": {
    "min_ms": 0.85,
    "mean_ms": 558.559,
    "p50_ms": 2.543,
    "p95_ms": 4046.057,
    "p99_ms": 9295.53,
    "max_ms": 10432.984
  },
  "checkout_ms": {
    "min_ms": 138.258,
    "mean_ms": 15382.681,
    "p50_ms": 13153.175,
    "p95_ms": 36221.522,
    "p99_ms": 38966.002,
    "max_ms": 41283.5
  },
  "payment_ms": {
    "min_ms": 0.585,
    "mean_ms": 450.897,
    "p50_ms": 3.29,
    "p95_ms": 3328.604,
    "p99_ms": 8611.212,
    "max_ms": 10388.369
  },
  "total_ms": {
    "min_ms": 142.539,
    "mean_ms": 15830.423,
    "p50_ms": 13705.091,
    "p95_ms": 36801.839,
    "p99_ms": 39464.734,
    "max_ms": 41290.492
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
      "user": 82,
      "checkout_status": 503,
      "queue_ms": 17.881167004816234,
      "checkout_service_ms": 10423.804249992827,
      "checkout_ms": 10441.685416997643,
      "checkout_error": "数据库暂时繁忙，请稍后重试",
      "total_ms": 10441.686500009382
    },
    {
      "user": 134,
      "checkout_status": 503,
      "queue_ms": 14.203958009602502,
      "checkout_service_ms": 10432.98408400733,
      "checkout_ms": 10447.188042016933,
      "checkout_error": "数据库暂时繁忙，请稍后重试",
      "total_ms": 10447.189167025499
    },
    {
      "user": 87,
      "checkout_status": 201,
      "queue_ms": 25.295083003584296,
      "checkout_service_ms": 114.27608400117606,
      "checkout_ms": 139.57116700476035,
      "order_id": "HM202611173C9788C5C5FF",
      "payment_ms": 10307.717833988136,
      "payment_status": 503,
      "payment_error": "数据库暂时繁忙，请稍后重试",
      "total_ms": 10447.29108302272
    },
    {
      "user": 64,
      "checkout_status": 503,
      "queue_ms": 26.213500008452684,
      "checkout_service_ms": 10432.026916998439,
      "checkout_ms": 10458.240417006891,
      "checkout_error": "数据库暂时繁忙，请稍后重试",
      "total_ms": 10458.241542015458
    },
    {
      "user": 331,
      "checkout_status": 503,
      "queue_ms": 269.5722500211559,
      "checkout_service_ms": 10290.144208003767,
      "checkout_ms": 10559.716458024923,
      "checkout_error": "数据库暂时繁忙，请稍后重试",
      "total_ms": 10559.717458003433
    },
    {
      "user": 383,
      "checkout_status": 201,
      "queue_ms": 351.49037500377744,
      "checkout_service_ms": 0.9227080154232681,
      "checkout_ms": 352.4130830192007,
      "order_id": "HM20261117069D4C5FAAB4",
      "payment_ms": 10297.853041003691,
      "payment_status": 503,
      "payment_error": "数据库暂时繁忙，请稍后重试",
      "total_ms": 10650.267917022575
    },
    {
      "user": 448,
      "checkout_status": 503,
      "queue_ms": 453.69112500338815,
      "checkout_service_ms": 10326.397917000577,
      "checkout_ms": 10780.089042003965,
      "checkout_error": "数据库暂时繁忙，请稍后重试",
      "total_ms": 10780.090208019828
    },
    {
      "user": 1030,
      "checkout_status": 201,
      "queue_ms": 1418.319208023604,
      "checkout_service_ms": 60.435624996898696,
      "checkout_ms": 1478.7548330205027,
      "order_id": "HM202611173D5CFBF87CAC",
      "payment_ms": 10311.614000005648,
      "payment_status": 503,
      "payment_error": "数据库暂时繁忙，请稍后重试",
      "total_ms": 11790.37045800942
    },
    {
      "user": 1245,
      "checkout_status": 503,
      "queue_ms": 1820.0052080210298,
      "checkout_service_ms": 10285.599541995907,
      "checkout_ms": 12105.604750016937,
      "checkout_error": "数据库暂时繁忙，请稍后重试",
      "total_ms": 12105.605750024552
    },
    {
      "user": 1533,
      "checkout_status": 201,
      "queue_ms": 2415.010541997617,
      "checkout_service_ms": 5.338833027053624,
      "checkout_ms": 2420.3493750246707,
      "order_id": "HM202611178E7B23D57417",
      "payment_ms": 10339.534875005484,
      "payment_status": 503,
      "payment_error": "数据库暂时繁忙，请稍后重试",
      "total_ms": 12759.886458021356
    }
  ]
}
```

## 库存 100 件

放行前提交：10000；准备提交耗时：0.0383秒；每秒完成买家：1813.84；应用错误日志：0条。

延迟分布（毫秒）：

```json
{
  "queue_ms": {
    "min_ms": 5.489,
    "mean_ms": 2237.387,
    "p50_ms": 2256.408,
    "p95_ms": 4071.302,
    "p99_ms": 4231.296,
    "max_ms": 4271.747
  },
  "checkout_service_ms": {
    "min_ms": 0.423,
    "mean_ms": 115.657,
    "p50_ms": 0.854,
    "p95_ms": 572.908,
    "p99_ms": 3249.758,
    "max_ms": 5378.793
  },
  "checkout_ms": {
    "min_ms": 143.612,
    "mean_ms": 2353.044,
    "p50_ms": 2356.98,
    "p95_ms": 4171.321,
    "p99_ms": 4733.787,
    "max_ms": 5512.592
  },
  "payment_ms": {
    "min_ms": 0.618,
    "mean_ms": 334.554,
    "p50_ms": 2.209,
    "p95_ms": 3002.469,
    "p99_ms": 4242.189,
    "max_ms": 4480.925
  },
  "total_ms": {
    "min_ms": 147.702,
    "mean_ms": 2356.39,
    "p50_ms": 2359.832,
    "p95_ms": 4172.541,
    "p99_ms": 4736.831,
    "max_ms": 5512.598
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

承载验收：通过；库存全部分配：是。

## 库存 1 件

放行前提交：10000；准备提交耗时：0.0467秒；每秒完成买家：1846.86；应用错误日志：0条。

延迟分布（毫秒）：

```json
{
  "queue_ms": {
    "min_ms": 4.205,
    "mean_ms": 2064.769,
    "p50_ms": 2063.533,
    "p95_ms": 3901.865,
    "p99_ms": 4058.998,
    "max_ms": 4097.802
  },
  "checkout_service_ms": {
    "min_ms": 0.401,
    "mean_ms": 113.723,
    "p50_ms": 0.849,
    "p95_ms": 566.048,
    "p99_ms": 3121.71,
    "max_ms": 5141.822
  },
  "checkout_ms": {
    "min_ms": 135.326,
    "mean_ms": 2178.492,
    "p50_ms": 2172.779,
    "p95_ms": 4000.603,
    "p99_ms": 4494.366,
    "max_ms": 5414.168
  },
  "payment_ms": {
    "min_ms": 5.318,
    "mean_ms": 5.318,
    "p50_ms": 5.318,
    "p95_ms": 5.318,
    "p99_ms": 5.318,
    "max_ms": 5.318
  },
  "total_ms": {
    "min_ms": 135.703,
    "mean_ms": 2178.493,
    "p50_ms": 2172.78,
    "p95_ms": 4000.604,
    "p99_ms": 4494.371,
    "max_ms": 5414.173
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

承载验收：通过；库存全部分配：是。

## 复现命令

```bash
python3 scripts/stress_purchase_wave.py --users 10000 --workers 256 --stocks 10000 100 1 --output reports/stress-purchase-10000.json
```

逐用户请求结果见 `stress-purchase-10000.json`。
