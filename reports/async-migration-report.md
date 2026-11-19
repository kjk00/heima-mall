# 异步商城改造与本地MySQL接入验证

日期：2026-11-17。执行环境：macOS / Python 3.12.15。

## 当前结论

项目已增加FastAPI异步入口，生产路径采用SQLAlchemy 2 AsyncSession、aiomysql和redis.asyncio；原有页面、管理后台及业务接口保留。默认启动选择异步后端，旧WSGI后端需显式指定。

**本地MySQL配置已写入 `.env`，但连接未成功验证。** 配置为 `127.0.0.1:3306/heima_mall`，默认root账号；密码需要与本机账号一致。`.env` 权限为600，未输出密码。发现 `/opt/homebrew/bin/mysql` 和 `/tmp/mysql.sock`；使用mysql客户端只读连接该套接字返回 `ERROR 2002 ... (1)`，当前执行环境禁止访问本地套接字。

**真实FastAPI/AsyncSession、MySQL、Redis和异步万人压测尚未执行。** `.venv` 缺少相关依赖，下载依赖曾因DNS/网络限制失败。提供了可执行入口和明确跳过的集成测试，不能把编译或兼容后端回归当作生产异步路径验证，也不能宣称已支持一万人同时成功下单。

## 实现内容

- `app/main.py`：异步端点和依赖、资源生命周期清理、请求准入、数据库错误映射、安全响应头。
- `app/async_database.py`：异步连接池，默认32条连接、无溢出；每次操作和整个业务事务默认10秒超时，失败或取消回滚。支持MySQL和独立SQLite测试。
- `app/models.py` / `app/commerce.py`：18个类型化ORM映射；购物车、下单、支付、取消和收货使用买家/订单锁，商品按编号逐个锁定，并先获取全部所需商品锁再修改。
- `app/logic.py` / `app/admin.py` / `app/features.py` / `app/shops.py`：数据库I/O改为await；前后台取消、退款、发货、地址和优惠券操作协调相关行锁。
- `app/async_cache.py`：异步Redis、公共目录版本key、本地锁与跨进程锁、持有者核对释放、取消清理、有界等待和故障回源。
- `app/request_guard.py`：ASGI正文大小限制、查询字段上限、原始JSON类型与范围校验；避免框架先把字符串/布尔值转换为整数。
- `app/security.py`：请求中的密码计算在线程执行；管理员初始化撤销已有普通会话。
- 订单新增请求指纹和兼容迁移，相同幂等键变更请求正文返回409；内部指纹不出现在公开响应。
- 图片发布前完整性/解码校验，修复仅有伪造文件头也能上传的问题。Pillow路径未在此环境运行；无依赖PNG路径已验证。旧测试PNG本身含错误CRC及截断结束块，已换为可验证的完整PNG，保留上传和回读断言。
- `scripts/check_mysql_async.py`：使用生产异步驱动检查本机MySQL；可选择初始化表结构。
- `scripts/stress_purchase_async.py`：独立ASGI请求波、分离虚拟买家和真实处理并发、报告延迟/错误/库存守恒；支持临时SQLite或随机MySQL测试库。

## 已执行验证

| 验证 | 结果 | 证据与边界 |
| --- | --- | --- |
| 完整回归 | 133项：110通过，23跳过，0失败 | `async-regression.log`；大部分业务经旧WSGI适配器与独立SQLite运行 |
| 异步控制与输入防护 | 9项通过 | `async-control-tests.log`；真实asyncio调度、内存缓存与可控事务连接，不是ORM/Redis驱动测试 |
| 企业补充验收 | 19项全部通过 | `async-enterprise-acceptance.log` 和 `async-enterprise-quality-results.json`；独立WSGI/SQLite |
| Python语法编译 | 通过 | app、scripts、tests及run.py |
| 服务函数调用静态核对 | 未发现遗漏await的直接服务调用 | 排除了旧WSGI的asyncio.run及返回协程的缓存loader；不能替代运行时验证 |
| ASGI/异步ORM测试 | 8项跳过 | 缺少FastAPI、SQLAlchemy、aiomysql、aiosqlite、greenlet、httpx等依赖 |
| 真实MySQL异步测试 | 8项跳过 | 需依赖、本地服务和 `MYSQL_ASYNC_TESTS=1` |
| 原有FastAPI/MySQL/Redis集成 | 7项跳过 | 依赖或真实服务条件不满足 |
| 本地MySQL异步检查 | 未执行连接 | `async-mysql-preflight.log`，退出2，说明缺少异步依赖 |
| 异步万人压测 | 未执行 | `async-load-preflight.log`，退出2，无性能测量 |

原企业补充验收中的旧会话权限升级、变更正文复用幂等键、截断图片上传缺陷在兼容路径均已回归通过。

## 本机运行步骤

先启动本地MySQL，检查 `.env` 中账号密码。然后在项目终端执行：

```bash
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python scripts/check_mysql_async.py --initialize
python run.py
```

成功启动后打开 `http://127.0.0.1:8000`。初始化需建库、建表及触发器权限；多进程运行前先单进程初始化，再设置 `MYSQL_INITIALIZE_SCHEMA=0`。

完成真实环境验证：

```bash
python -m unittest discover -v
MYSQL_ASYNC_TESTS=1 python -m unittest tests.test_async.MySQLAsyncORMTests -v
REDIS_TEST_URL=redis://127.0.0.1:6379/0 python -m unittest tests.test_services.RedisIntegrationTests -v
python scripts/stress_purchase_async.py --backend mysql --users 10000 --concurrency 256 --stocks 10000 100 1 --redis-url redis://127.0.0.1:6379/0 --hot-read --output reports/async-mysql-redis-wave.json
```

MySQL测试/压测使用新建的随机 `heima_test_async_*` 库，并在结束后删除该测试库；不操作应用库。Redis key使用独立数据库命名空间，不执行FLUSHDB。

## 容量与上线限制

旧 `stress-purchase-10000.md` 测量的是改造前WSGI/SQLite：10000库存场景创建9930单、70次下单503，9870单支付成功、60次支付503；未超卖，但未达到零服务失败的万人容量目标。该报告不能代表异步、MySQL或Redis能力。

异步脚本统一释放10000个买家，默认客户端并发256、应用处理准入128、连接池32；不含TCP、代理或网络。可用 `--concurrency 10000` 模拟万人同时向ASGI提交请求，但这仍不是一万人同时占用数据库连接或都成功完成下单。

共享商品/店铺目录版本行仍会串行化写入，保留模块仍有通用事务守卫；热点商品本身也需要竞争锁。大量写入时还可能出现死锁或超时并返回503，必须用真实MySQL测试决定限流、重试与后续分区方案。增加异步和Redis无法直接消除这些瓶颈。

登录限流按进程保存，支付/退款仍为演示状态变化，未接入真实资金、支付回调或待支付订单超时释放任务。当前交付是异步实现与测试入口，不是生产容量认证。
