# 黑马商城

一个可以本地运行的 Python 异步电商项目，包含多店铺商城、店铺管理、个人中心、优惠券、售后退款、订单跟踪及公共商品评论。默认使用 **FastAPI + SQLAlchemy 2 AsyncSession + aiomysql + redis.asyncio**，接入本地 MySQL；前端保留 HTML / CSS / 原生 JavaScript。SQLite 用于独立测试，标准库 WSGI 后端用于兼容原有本地开发流程。

## 立即运行

需要 **Python 3.12 或更高版本、MySQL 8.0.16 或更高版本**。先启动本地 MySQL，并在项目根目录配置连接：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp -n .env.example .env
chmod 600 .env
```

编辑 `.env` 中的 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_DATABASE`、`MYSQL_USER` 和 `MYSQL_PASSWORD`。默认连接 `127.0.0.1:3306/heima_mall`，用户为 `root`；如果本机 root 有密码，请填写实际密码。密码只保存在本机 `.env` 或环境变量中；`.env` 已被忽略，不应提交。应用自动读取 `.env`，已导出的环境变量优先。

先验证异步驱动连接并初始化表结构：

```bash
python scripts/check_mysql_async.py --initialize
```

仅检查已有数据库连接时省略 `--initialize`。如使用本机套接字，可在 `.env` 设置 `MYSQL_UNIX_SOCKET=/tmp/mysql.sock`。

启动商城：

```bash
python run.py
```

macOS 也可以在 Finder 中双击项目里的 `start.command`，它会自动进入项目目录、优先选择 `.venv` 并启动异步服务。终端显示 Uvicorn 的 `Application startup complete` 后，下面的地址才能访问。请保持启动服务的终端窗口打开；关闭窗口或按 `Ctrl+C` 会停止服务。

打开：

- 商城：<http://127.0.0.1:8000>
- 管理后台：<http://127.0.0.1:8000/admin>
- 可交互的接口文档：<http://127.0.0.1:8000/docs>
- OpenAPI：<http://127.0.0.1:8000/openapi.json>

首次启动自动创建配置的 MySQL 数据库与 InnoDB 表，导入 12 件演示商品及演示店铺。再次启动会保留用户、购物车、订单和库存。初始化账号需要建库、建表及创建触发器权限；可先用本地管理账号初始化，再设置 `MYSQL_INITIALIZE_SCHEMA=0` 并使用具备读写业务表权限的账号运行。图片、图标和字体设置均在本地，无需外部图床或 CDN。

待支付订单的支付期限为创建后 **15 分钟**。商城运行时每 5 秒检查超时订单，自动取消并恢复预留库存、释放优惠券；启动时会补处理停机期间过期的订单。支付接口在事务中再次检查期限，满 15 分钟后拒绝支付。订单窗口显示剩余支付时间，超时关闭后显示“已过期”；接口保留 `status=cancelled`，通过 `is_expired` 区分超时与手动取消，并返回 `payment_expires_at` 和 `server_time`。

继续使用 SQLite 时，安装 `requirements-dev.txt` 中的 `aiosqlite`，在 `.env` 设置 `DATABASE_BACKEND=sqlite` 和 `DATABASE_PATH=data/mall.db`。显式执行 `python run.py --backend stdlib` 可使用旧 WSGI 后端；它配合 SQLite 不需要数据库第三方依赖。原有 SQLite 文件会保留，MySQL 接入不会自动迁移或删除它。

需要迁移已有数据时，先停止商城写入，将 `MYSQL_DATABASE` 设置为一个新数据库，再执行：

```bash
python3 scripts/migrate_sqlite_to_mysql.py --source data/mall.db
```

迁移工具以只读快照读取 SQLite，按外键顺序复制账号、会话、商品、订单及扩展业务表，在一个目标事务中逐表验证行数。目标库已有任何业务数据时拒绝迁移；源文件保留。上传素材仍在 `data/uploads/`，切换数据库时也应保留它。

端口被占用时使用 `python run.py --port 8001`。按 `Ctrl+C` 停止服务。

## 开始使用后台

如果商城服务已经在运行，更新代码后需要先在启动服务的终端按 `Ctrl+C`，再运行 `./start.command`。后台、商城和所有接口使用同一个端口。

1. 打开 <http://127.0.0.1:8000/admin>。
2. 首次使用时，页面会显示“创建终端管理员账号”，填写昵称、手机号和密码。
3. 如手机号已在商城注册，需要填写该账号的现有密码，验证通过后该账号成为管理员。
4. 初始化完成后使用管理员账号登录，登录需要填写图形验证码。普通商城账号没有管理权限；首次初始化只允许从服务器本机访问，且只能执行一次。

管理员分为两类：**终端管理员**可查看和管理全部店铺；**店铺管理员**只查看和管理绑定店铺的商品、订单、客服、优惠券、退款、评论及该店顾客的消费统计。新增店铺和启用、停用商城账号仅允许终端管理员操作。主页的管理后台入口默认隐藏，登录后确认具备管理权限才显示，退出或会话失效后隐藏。

已有平台管理员自动保留为终端管理员，账号和密码不变。在服务器终端设置已注册账号的权限（设置后该账号需要重新登录）：

```bash
python3 manage_admin.py --phone 13800138000 --store-id 2
python3 manage_admin.py --phone 13800138000 --super-admin
```

为尚未分配管理员的店铺随机生成账号，每家店绑定一个独立账号：

```bash
python3 manage_admin.py --generate-store-admins
```

登录信息保存在 `data/store-admin-accounts.md`，文件仅当前系统用户可读写；已有账号和店铺绑定不会被覆盖。指定 `--database` 可操作其他数据库，`--output` 可选择新的登录信息文件。权限只允许从服务器终端设置，商城注册接口不能指定管理员角色或店铺权限。

全平台优惠券由终端管理员管理；店铺管理员创建的优惠券自动归属本店，并且只能用于本店订单。

后台提供：

- **经营概览**：真实订单的支付总额、订单数、用户数、在售商品数、近 7 天下单趋势、待发货和库存提醒。日期按北京时间统计。
- **店铺管理**：终端管理员可新增和管理所有店铺；店铺管理员只管理本店名称、介绍、客服电话、基础运费、包邮门槛及营业状态，查看本店商品、订单、净实付金额和客服会话。
- **商品管理**：新增、编辑、选择所属店铺、分类、售价与划线价、可售库存、商品详情、标签、背景色、上下架和搜索分页。商品、订单与客服会话均可按店铺筛选。
- **商品图片**：选择本地素材，或上传最大 2 MB 的静态 PNG、JPEG、WebP 图片。发布前验证并解码图片，限制每边最多10000像素、总计最多1600万像素；截断文件与伪造文件头被拒绝。未安装 Pillow 的旧后端只支持经校验的非交错PNG。上传图片存放在数据库所在目录的 `uploads/` 中。
- **订单管理**：搜索、状态筛选、订单详情、关闭未支付订单、填写物流公司和单号后发货。
- **店铺客服**：按店铺查看顾客会话、未读消息和历史记录并人工回复；同一用户在不同店铺有独立会话，消息每 5 秒同步。
- **优惠券管理**：创建满减券或折扣券，设置门槛、有效期、领取上限和启用状态。已领取券的优惠规则固定，可新建不同规则的券。
- **退款审核**：批准或拒绝整单演示退款。未发货订单自动恢复库存；已发货订单仅在确认退货已收到并可销售后勾选恢复库存。
- **评论与评价**：查看公共评论或购买评价，隐藏或恢复显示内容。
- **用户管理**：查看账号、订单数和已支付金额；启用、停用普通用户。停用会立即撤销该用户的登录会话，管理员账号受保护。

后台与商城直接读写同一数据库。商品保存后，商城接口立即返回新信息；下架商品会从商城隐藏，且无法继续加入购物车或下单。不同标签页之间会通知刷新，页面切回前台或每 30 秒也会同步数据。

在订单详情中可更新“打包中”，填写物流公司和单号后进入“已发货”，再手动更新为“运输中”或“已到货”。用户的“我的订单”显示进度条与时间线，并可确认收货。物流由店铺更新，不自动模拟快递轨迹。支付状态仍为 `pending` / `paid` / `cancelled`，发货进度使用 `fulfillment_status` 的 `unfulfilled` / `shipped` / `completed` 表示。

后台与商城共用 Cookie 登录会话。在任一页面退出或切换账号，其他标签页会同步登录状态；需要同时体验管理员与客户时，可用不同浏览器或隐私窗口登录。

商品和库存采用版本检查：如果打开编辑窗口后发生了下单、取消订单或其他管理员修改，旧版本保存会被拒绝。重新打开编辑窗口可获得最新库存，避免覆盖真实库存变化。已有数据库会自动增补管理所需字段，保留账号、购物车、订单、库存和原有登录会话。

## 店铺、搜索与结算

每件商品属于一家店铺。点击商品图片或名称进入独立商品详情页（如 `/products/1`），显示图片、名称、价格、库存、销量、商品介绍、公共评论及购买评价。详情页显示所属店铺和配送规则，点击“进入店铺”前往店铺页（如 `/stores/2`），查看店铺介绍及该店在售商品，也可联系本店客服。商品页与店铺页支持直接打开链接、刷新和浏览器返回；商品下架或店铺暂停营业时显示提示。

点击搜索框，显示当前账号最近浏览的商品、相关品类的热门商品及热门店铺。未登录用户看到公开热门推荐，其他账号的浏览记录不会出现在推荐中。输入商品名或店铺名均可搜索；搜索店铺名时显示店铺卡片及店内商品，点击“进入店铺”浏览该店铺。推荐基于浏览记录、商品品类与销量排序。

搜索框还显示最近搜索的关键词，点击记录可再次搜索，也可一键清空。仅记录实际提交的非空搜索，重复关键词移到最前，最多保留10条。记录保存在当前浏览器，刷新后保留；不同账号与未登录访客分别保存。

购物车按店铺分组，点击“结算本店”只创建该店铺的订单，保留其他店铺的购物车商品。每家店独立计算运费、包邮和优惠门槛。一次结算对应一张店铺订单，每单最多用一张全平台优惠券。

后台“店铺管理”可配置基础运费与包邮门槛，门槛为0表示始终包邮；默认满199元包邮，不足收取10元运费。暂停营业后，本店商品不在商城展示，无法新增购物车、下单或支付待付款订单。已支付订单仍可发货和处理售后，既有客服会话仍可查看工作人员的历史及后续回复。

已有数据库会自动增补店铺归属。未分配的商品按品类归入演示店铺，之后的后台调整会保留。历史订单的店铺名称及运费保留快照，不随商品移店或店铺改名而变化；旧客服消息归入“黑马自营店”，聊天消息编号与已读位置保留。

## 个人中心、优惠券与商品讨论

登录后点击右上角昵称，进入个人中心：

- **店铺客服**：选择店铺发送商品或订单问题，工作人员从对应会话回复。未读提醒可在账号设置中关闭；查看消息后才更新已读状态。
- **地址管理**：新增、编辑、删除地址并设置默认地址；结算可选择保存的地址，也可手动填写并保存。订单保留下单时的地址快照。
- **账号设置**：修改昵称、客服提醒和密码。修改密码保留当前登录，撤销其他设备会话。
- **我的优惠券**：领取和查看优惠券。导航“领券中心”进入同一页面；默认提供满99减10、满199享9折（最多减50元）两种演示券。
- **浏览历史**：显示当前账号看过的商品详情，最近查看的排在前面，可清空记录。

导航“折扣专区”显示划线价高于售价的在售商品。结算可以使用一张已领取、未使用且在有效期内的优惠券。使用门槛及包邮按本店优惠前商品金额计算，优惠只抵扣商品金额。未支付订单取消或整单退款批准后，优惠券恢复可用；已过期的券仍不能使用。

每件商品的详情页包含两个独立区域：**公共评论区**允许登录用户讨论，访客也可浏览；**购买评价**要求当前账号购买并支付过该商品，支持1–5分评分及修改自己的评价。退款中的购买不能作为评价凭据。公开内容只显示昵称、时间、正文及评分，不公开手机号或订单资料。后台隐藏的评价在作者修改后仍保持隐藏。

“我的订单”中，已支付订单可申请整单演示退款并查看审核说明；被拒绝后可重新申请。退款处理中暂停发货、物流更新及确认收货。审核通过后，从后台收入和用户消费金额中扣除订单实付，减少商品销量；重复审核不会重复更新库存或金额。支付和退款均不涉及真实资金。

## 异步架构

所有 API 端点与数据库业务函数使用 `async def`。数据库请求使用 SQLAlchemy 异步连接池和 `AsyncSession`；购物车、下单、支付、取消及收货使用类型化 ORM 模型，其他业务模块通过同一异步会话执行参数化 SQL。密码哈希和图片文件操作放在线程中执行，避免阻塞事件循环。这里选用SQLAlchemy异步ORM，不需要同时引入SQLModel、Tortoise或Django。

已有虚拟环境时，安装依赖并启动：

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
python run.py --backend fastapi
```

开发时也可以执行：

```bash
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

`python run.py` 和 `--backend auto` 都选择异步 FastAPI；依赖缺失会明确报错。旧后端必须显式选择 `--backend stdlib`，它在独立WSGI线程中运行同步数据库兼容适配器，不属于ASGI异步性能路径。

默认每进程最多32条数据库连接、不启用溢出连接；API最多128个请求进入处理，排队超过3秒返回503和 `Retry-After: 1`。数据库操作与整个业务事务默认最多10秒，超时返回503，取消请求或失败的事务回滚。ASGI入口限制正文大小（普通请求16KB、图片上传JSON 3MB）、最多20个查询字段，并在框架自动转换类型前校验原始JSON。数据库错误不会向客户端暴露SQL或连接密码。

先用单进程初始化数据库，再在 `.env` 设置 `MYSQL_INITIALIZE_SCHEMA=0` 后运行 `python run.py --workers 4`。连接池、准入和登录限流均按进程独立，4个进程配置 `DB_POOL_SIZE=32` 最多占用128条数据库连接；需按本机MySQL连接上限调整。

## 功能

- 商品：首页精选、分类、搜索、价格排序、人气排序、新品排序、分页、商品详情。
- 用户：地址管理、昵称与密码设置、店铺客服、浏览历史；手机号注册、密码登录、退出登录、会话保持；注册后自动登录。登录（含后台登录）需填写本地生成的图形验证码，防止脚本批量尝试密码。没有预置账号，请使用测试信息注册，密码至少 8 位。
- 购物车：按账号保存、增减数量、删除商品、计算总额和运费。
- 结算：选择店铺和地址，选择优惠券，创建订单后移除本店购物车商品。
- 订单：列表、详情、演示支付、取消未支付订单、退款申请、配送阶段时间线、物流信息和确认收货；不同账号只能访问自己的订单。
- 管理后台：经营统计、商品和图片管理、订单发货、用户启用停用，以及独立的管理员权限校验。
- 库存：下单扣减库存，取消订单恢复库存。异步核心交易使用InnoDB事务、买家与订单行锁、按商品编号获取的商品锁；前后台取消、退款和发货也协调订单锁。SQLite使用 `BEGIN IMMEDIATE` 写事务。保留模块仍有共享事务守卫，商品与店铺写入还会更新共享目录版本行；这些瓶颈仍需真实MySQL压测评估，异步改造本身不能证明万人容量。
- 幂等：下单传入 `idempotency_key`，同一请求重试返回原订单；新订单会保存请求指纹，同一键换地址、优惠券或店铺返回409。历史订单无指纹时保留兼容行为。每一笔新订单使用新的键；支付和取消也支持重复请求。

所有金额在数据库和 API 中使用**整数分**，页面展示为人民币元。每家店默认商品满 **199 元包邮**，不足时收取 **10 元运费**，可在店铺管理调整。订单保存商品名称、图片和价格快照，商品信息更新不会改变已有订单。

## 接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/stores?q=店铺名` | 搜索营业中的店铺 |
| GET | `/api/stores/{store_id}` | 店铺详情 |
| GET | `/api/search/suggestions` | 最近浏览、兴趣品类热门商品与店铺推荐 |
| GET / POST | `/api/admin/stores` | 管理店铺列表 / 新增店铺 |
| GET / PUT | `/api/admin/stores/{store_id}` | 管理详情 / 编辑店铺 |
| PATCH | `/api/admin/stores/{store_id}/status` | 暂停 / 恢复营业 |
| GET / POST | `/api/addresses` | 地址列表 / 新增 |
| PUT / DELETE | `/api/addresses/{address_id}` | 编辑 / 删除本人地址 |
| GET / PUT | `/api/settings` | 获取 / 修改设置 |
| POST | `/api/settings/password` | 修改密码 |
| GET | `/api/coupons` | 优惠券与领取状态 |
| POST | `/api/coupons/{coupon_id}/claim` | 领取优惠券 |
| GET / POST / DELETE | `/api/history` | 查看 / 记录 / 清空浏览历史 |
| GET / POST | `/api/products/{product_id}/comments` | 公共评论列表 / 发布 |
| GET / PUT | `/api/products/{product_id}/reviews` | 购买评价列表 / 保存自己的评价 |
| POST | `/api/orders/{order_id}/refund` | 申请整单演示退款 |
| GET | `/api/support/conversations` | 本人咨询过的店铺及未读总数 |
| GET / POST | `/api/support/messages` | 本人指定店铺会话 / 发送消息 |
| POST | `/api/support/read` | 标记已展示消息为已读 |
| GET / POST | `/api/admin/coupons` | 优惠券管理 / 新增 |
| PUT | `/api/admin/coupons/{coupon_id}` | 编辑优惠券 |
| GET | `/api/admin/refunds` | 退款审核列表 |
| POST | `/api/admin/refunds/{refund_id}/decision` | 批准 / 拒绝退款 |
| POST | `/api/admin/orders/{order_id}/tracking` | 更新配送阶段 |
| GET | `/api/admin/discussions?kind=comments` | 管理公共评论，kind=reviews 查看购买评价 |
| PATCH | `/api/admin/discussions/{comment_id}?kind=comments` | 隐藏 / 恢复内容 |
| GET | `/api/admin/support` | 客服会话列表 |
| GET / POST | `/api/admin/support/{conversation_id}/messages` | 查看 / 回复会话 |
| POST | `/api/admin/support/{conversation_id}/read` | 客服标记已读 |
| GET | `/api/health` | 健康检查 |
| GET | `/api/categories` | 分类列表 |
| GET | `/api/products` | 商品搜索、分类、排序和分页 |
| GET | `/api/products/{product_id}` | 商品详情 |
| POST | `/api/auth/register` | 注册并登录 |
| GET | `/api/auth/captcha` | 获取登录图形验证码（5 分钟有效） |
| POST | `/api/auth/login` | 登录（需 captcha_id 与 captcha_code） |
| GET | `/api/auth/me` | 当前用户 |
| POST | `/api/auth/logout` | 退出登录 |
| GET / POST | `/api/cart` | 查看购物车 / 添加商品 |
| PATCH / DELETE | `/api/cart/{product_id}` | 更新数量 / 移除商品 |
| GET / POST | `/api/orders` | 订单列表 / 创建订单 |
| GET | `/api/orders/{order_id}` | 订单详情 |
| POST | `/api/orders/{order_id}/pay` | 演示支付 |
| POST | `/api/orders/{order_id}/cancel` | 取消未支付订单 |
| POST | `/api/orders/{order_id}/receive` | 确认收货 |
| GET / POST | `/api/admin/setup` | 初始化状态 / 首次创建管理员 |
| POST | `/api/admin/auth/login` | 管理员登录 |
| GET | `/api/admin/auth/me` | 当前管理员 |
| GET | `/api/admin/dashboard` | 经营概览 |
| GET | `/api/admin/options` | 商品分类及图片素材 |
| GET / POST | `/api/admin/products` | 管理商品列表 / 新增商品 |
| GET / PUT | `/api/admin/products/{product_id}` | 详情 / 编辑商品与库存 |
| PATCH | `/api/admin/products/{product_id}/status` | 商品上下架 |
| POST | `/api/admin/images` | 上传商品图片 |
| GET | `/api/admin/orders` | 管理订单列表 |
| GET | `/api/admin/orders/{order_id}` | 管理订单详情 |
| POST | `/api/admin/orders/{order_id}/ship` | 发货并填写物流 |
| POST | `/api/admin/orders/{order_id}/cancel` | 关闭未支付订单 |
| GET | `/api/admin/users` | 用户列表及消费统计 |
| PATCH | `/api/admin/users/{user_id}/status` | 启用或停用普通用户 |

创建订单传入 `store_id` 选择店铺；仅有一家店铺的购物车可省略，多店购物车省略会返回409。商品搜索的 `q` 同时匹配商品名和店铺名，`store_id` 可限定店铺。客服消息 GET 使用 `store_id` 查询参数，发送消息及已读 POST 在正文传入同一 `store_id`。旧接口请求不传店铺时，兼容指向黑马自营店（编号1）。店铺列表、商品与订单管理支持分页，店铺管理还可按名称和营业状态筛选。

写请求必须携带 `X-Requested-With: HeimaMall` 标头。浏览器提交的 Origin 必须与服务端同源。登录后通过 HttpOnly Cookie 认证，不需要在前端存储 Token。

登录和后台登录必须先调用 `GET /api/auth/captcha`，返回 `captcha_id` 和 Base64 PNG 图片；登录正文携带 `captcha_id` 与图片中的 4 位 `captcha_code`（不区分大小写）。验证码 5 分钟有效、一次性使用，校验失败即作废，需重新获取。验证码由标准库本地生成，不依赖外部服务；获取接口按 IP 每分钟限 30 次。

`/docs` 使用本地脚本，可直接选择接口、填写参数、发送请求并查看 JSON 响应。该页面会操作当前数据库，登录与商城共用 Cookie。

## 项目结构

```text
heima-mall/
├── run.py                   # 启动入口
├── app/
│   ├── main.py              # 异步FastAPI入口、准入与生命周期
│   ├── async_database.py    # AsyncSession、aiomysql连接池与事务
│   ├── models.py            # 类型化SQLAlchemy ORM实体
│   ├── commerce.py          # 异步核心交易与行锁
│   ├── async_cache.py       # redis.asyncio公共目录热点缓存
│   ├── async_contract.py    # 异步事务契约、旧WSGI兼容适配
│   ├── request_guard.py     # 有界ASGI正文和原始JSON校验
│   ├── image_validation.py  # 上传图片解码校验
│   ├── server.py            # 兼容旧流程的WSGI HTTP入口
│   ├── logic.py             # 商品、用户、购物车和订单业务
│   ├── admin.py             # 管理员权限、商品、订单和用户管理
│   ├── shops.py             # 店铺管理、商品归属与搜索推荐
│   ├── features.py          # 地址、优惠、售后、讨论、历史及客服业务
│   ├── database.py          # 数据库选择、SQLite表结构与共用事务
│   ├── mysql.py             # 本地MySQL连接与SQL适配
│   ├── mysql_schema.sql     # MySQL/InnoDB表结构
│   ├── cache.py             # Redis公共目录热点缓存
│   ├── search.py            # ES检索路由、wildcard查询与故障降级
│   ├── search_sync.py       # outbox→RabbitMQ→ES同步、对账与别名重建
│   ├── config.py            # 本机.env配置加载
│   ├── schemas.py           # 两个后端共用的请求校验
│   ├── security.py          # 密码哈希与会话
│   ├── media.py             # 上传图片的公开访问
│   ├── routes.py            # 路由及 OpenAPI 文档
│   ├── seed.py              # 演示商品
│   ├── templates/           # 商城、后台与接口文档页面
│   └── static/              # CSS、JavaScript、本地 SVG 图片
├── scripts/generate_assets.py
├── tests/                   # HTTP 接口与并发库存测试
├── scripts/check_mysql_async.py    # 检查/初始化本地MySQL异步连接
├── scripts/stress_purchase_async.py # 独立ASGI并发下单压测
├── scripts/stress_search.py   # 商品目录搜索压测（唯一命中/同类关键词/深翻页，可选ES）
├── scripts/reindex_search.py  # 手动全量重建ES搜索索引
├── requirements.txt         # 异步服务依赖
└── requirements-dev.txt     # ASGI/SQLite及其他测试依赖
```

## 验证

无需安装任何依赖即可运行核心接口测试：

```bash
python3 -m unittest discover -v
```

测试会使用临时数据库，通过真实 WSGI 请求入口调用接口，不监听网络端口、不修改商城数据库。覆盖注册登录、会话过期、参数校验、购物车、运费、订单快照、支付、取消、跨账号访问、并发抢最后一件商品以及重复提交，并覆盖登录验证码的缺失、错误、一次性使用、过期与获取限流。

后台测试还覆盖首次初始化与权限隔离、商品更新同步到商城和购物车、下架拦截、图片上传、发货和确认收货、账号停用与会话撤销、并发库存与管理员编辑冲突，以及旧数据库兼容升级。

店铺测试覆盖店铺管理权限和版本冲突、商品归属、单店结算与运费、暂停营业拦截、订单快照、各店客服隔离、旧会话迁移、店铺名称搜索与推荐数据隔离。

页面测试覆盖商品和店铺链接的直接访问、刷新及详情数据关联。有 Node.js 时，还会执行真实前端脚本的模拟 DOM 测试，检查商品点击、进店筛选、搜索、浏览器返回、异步响应顺序及加载失败提示；未安装 Node.js 时会明确跳过这一项。

扩展功能测试还覆盖默认地址与权限、密码修改后的会话撤销、优惠券领取并发与整数分抵扣、退款幂等和库存/收入变化、物流阶段约束、浏览历史隔离、购买评价验证、公共评论管理，以及客服消息分页与已读竞态。

无需第三方依赖的异步控制测试会验证事件循环、热key回源合并、锁释放、取消回滚、流式正文上限和原始数值类型。安装 `requirements-dev.txt` 后，同一命令还会运行真实FastAPI/AsyncSession测试（独立SQLite），覆盖最后一件库存竞争、前后台支付/取消竞争、事务超时/取消回滚、幂等指纹、地址优惠券及异步路由。未安装时会明确显示跳过原因。

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -v
```

测试使用显式临时 SQLite 数据库，覆盖业务回归、缓存击穿保护、缓存版本一致性、Redis 故障降级、MySQL SQL 方言与结果类型以及迁移工具。缓存单元测试使用可控的内存后端；它们不等同于真实 Redis 集成测试。当前环境无法下载依赖或访问本地数据库套接字，真实 MySQL、Redis、FastAPI 和浏览器验证需要在允许连接服务的环境中执行。

本地 MySQL 和 Redis 可用后，执行真实服务集成测试。MySQL 测试创建独立的 `heima_test_*` 数据库并在结束后删除；Redis 测试只清理自己的随机命名空间，不执行 `FLUSHDB`：

```bash
MYSQL_TESTS=1 python3 -m unittest tests.test_services.MySQLIntegrationTests -v
MYSQL_ASYNC_TESTS=1 python -m unittest tests.test_async.MySQLAsyncORMTests -v
REDIS_TEST_URL=redis://127.0.0.1:6379/0 python3 -m unittest tests.test_services.RedisIntegrationTests -v
SEARCH_INTEGRATION_TESTS=1 .venv/bin/python -m unittest tests.test_search_integration -v
```

ES 搜索集成测试需要本机 Elasticsearch 与 RabbitMQ 在线，使用独立临时 SQLite 与按路径哈希隔离的索引/队列，结束后清理自己的别名与队列，不触碰商城数据；覆盖增量增改删同步到 ES、对账修复漂移、别名切换重建，以及两个后端经真实 HTTP 入口搜索命中 ES 文档。搜索路由与 outbox 同步链路的单元测试（`tests/test_search.py`）全部使用可控 stub，无需真实 ES/RabbitMQ，包含在上面的 `unittest discover` 中。

企业级补充验收及万人到达压测：

```bash
python3 scripts/enterprise_quality.py
python3 scripts/stress_purchase_wave.py --users 10000 --workers 256 --stocks 10000 100 1
```

上面的旧压测使用临时 SQLite 和 WSGI 请求入口，万人指统一提交的虚拟买家，实际处理并发受工作线程限制。历史结果见 `reports/stress-purchase-10000.md`，它是接入MySQL、Redis及异步架构之前的测量，不能作为新后端的容量证明。

异步ASGI压测使用HTTPX `ASGITransport` 和真实AsyncSession，不监听网络端口。测试预置独立买家、会话和购物车，再统一释放10000个虚拟买家；实际并发由客户端、应用准入和数据库连接池共同限制。报告包含排队/处理/总延迟、成功订单吞吐、HTTP失败、库存/销量守恒及数据库一致性；它不包含真实TCP连接、代理和网络延迟。

```bash
python scripts/stress_purchase_async.py --users 10000 --concurrency 256 --stocks 10000 100 1
python scripts/stress_purchase_async.py --backend mysql --users 10000 --concurrency 256 --stocks 10000 100 1 --redis-url redis://127.0.0.1:6379/0 --hot-read --output reports/async-mysql-redis-wave.json
```

SQLite场景仅使用临时文件；MySQL场景强制创建随机 `heima_test_async_*` 库并在结束后删除，需要测试账号有建库、触发器和删除测试库权限。Redis使用测试数据库自己的命名空间，不清空现有Redis数据。`--concurrency 10000` 可提交万人同时请求波，但仍受服务端准入限制。依赖不足时脚本会退出并说明未执行，不产生性能结论。最新改造与验证情况见 `reports/async-migration-report.md`。

搜索压测在独立临时 SQLite 中按 `--products` 铺商品（默认 1 万件，含 1 件唯一目标商品与 1200 件含同一关键词的同类商品；`--products 1000000 --only unique` 可复现百万级唯一商品搜索），对比唯一商品搜索、同类关键词搜索、深翻页与无关键词浏览在并发 1/16/64 下的吞吐与延迟分位；`--backend stdlib` 走 WSGI 多线程，`--backend fastapi` 走 ASGI 异步并发（需 requirements-dev 依赖）。默认关闭 Redis 目录缓存，测得搜索引擎本身的速度。设置 `ELASTICSEARCH_URL` 与 `RABBITMQ_URL` 后压测改走 ES：脚本先铺数据再重建索引（FastAPI 由生命周期内的同步任务独占重建并轮询就绪，stdlib 无同步任务则由脚本重建），随后测量经真实请求入口的 ES 搜索：

```bash
python3 scripts/stress_search.py
.venv/bin/python scripts/stress_search.py --backend fastapi --output reports/stress-search-fastapi.json
ELASTICSEARCH_URL=http://127.0.0.1:9200 RABBITMQ_URL=amqp://guest:guest@127.0.0.1:5672/ DATABASE_BACKEND=sqlite \
  .venv/bin/python scripts/stress_search.py --backend stdlib --products 1000000 --only unique keyword browse \
  --output reports/stress-search-1m-es.json
```

百万级实测（独立临时 SQLite，100 万件商品）：数据库 `LIKE` 唯一商品搜索 P95 约 1.16 秒、并发 64 时升至约 57 秒（见 `reports/stress-search-1m.md`）；引入 ES 后同一场景 P95 降至约 5 毫秒、并发 64 约 168 毫秒，吞吐与延迟分位见 `reports/stress-search-1m-es.md` 与 `reports/stress-search-1m-es-fastapi.md`。ES 为异步派生副本，该对比只反映搜索读路径，不含商品写入到可搜索的同步延迟。

## Redis 热点缓存

安装 `redis` 依赖并启动本地 Redis 后，在 `.env` 设置 `REDIS_URL=redis://127.0.0.1:6379/0`。留空则直接读取数据库。`REDIS_CACHE_TTL_SECONDS` 默认为30秒，实际过期时间加入最多20%的随机偏移。

缓存覆盖公共商品详情、商品列表、分类、店铺详情和店铺列表；账号、购物车、订单及个性化推荐继续直接访问数据库。每个数据库独立命名空间，key 包含数据库实例标识、商品/店铺版本及规范化查询摘要。产品或店铺的新增、修改、删除通过数据库触发器在同一事务中推进版本，旧缓存随TTL过期，不会在Redis失联后恢复时重新命中新版本请求。

FastAPI使用 `redis.asyncio`，连接池最多64条，不启用自动重试。同进程使用 `asyncio.Lock` 合并热点回源，跨进程使用Redis的 `SET NX EX` 锁，释放锁时用Lua核对持有者；取消回源任务会释放锁。连接和命令超时为100毫秒，等待回填最多200毫秒；故障后暂停访问Redis两秒并回源数据库。库存、订单、支付和退款判断仍在数据库事务内执行；缓存命中仍读取一行数据库版本号，因此缓存主要减少目录详情的业务查询，不消除所有数据库读请求。

## Elasticsearch 商品搜索

数据量到达百万级、追求搜索性能时，可引入 Elasticsearch 承担商品检索，MySQL/SQLite 仍是唯一权威数据源。同时设置 `ELASTICSEARCH_URL` 与 `RABBITMQ_URL` 即启用；任一留空则完全回到数据库 `LIKE` 搜索，不产生任何额外开销。依赖见 `requirements.txt` 的 `elasticsearch`、`aiohttp`、`aio-pika`。

**同步链路（事务 outbox → RabbitMQ → 消费者）**：商品的新增、修改、删除由数据库触发器在**同一事务**内写入 `product_sync_outbox` 表；中继任务批量取出商品编号，经发布确认（publisher confirms）投递到持久化 RabbitMQ 队列，**确认成功后才删除** outbox 行；消费者重新读取这些商品的**当前**数据库状态，存在则 upsert、已删除则从 ES 移除，批量写入成功后才 ack，失败则 nack 重新入队。因为消费端总是回读最新行，整条链路幂等、与顺序无关，重复投递和崩溃重启都会收敛到数据库状态。FastAPI 在生命周期内运行该同步任务；旧 WSGI 后端由 `run.py` 启动一个专用守护线程运行同一任务。

**搜索路由**：`GET /api/products` 先向 ES 发起 wildcard 查询（等价于 `LIKE '%关键词%'`，大小写不敏感，转义 `\ * ?`），只取回命中商品的 `id` 与总数，再按 ES 顺序回表读取最新行并装配店铺信息；营业状态过滤、店铺名匹配都在查询时实时读数据库，店铺改名或停业立即生效，无需重建索引。ES 返回的总数用于分页。深翻页（`from+size` 超过 10000）或 ES 故障时自动降级为数据库查询：故障会触发 5 秒熔断并限频告警，期间搜索走 SQL，恢复后自动回到 ES。

**对账与重建**：同步任务周期性（默认 300 秒）比对数据库与 ES 的商品数量和版本校验和，发现漂移或别名缺失即全量重建。重建在带随机后缀的新索引中批量写入，刷新后通过**原子别名切换**对外生效，再删除旧索引；失败会清理半成品索引。每个数据库按路径哈希独立命名别名（`heima_products-<hash>`）与队列（`heima.product.sync.<hash>`），多套数据互不干扰。手动全量重建：

```bash
python scripts/reindex_search.py
```

**本机安装（macOS arm64）**：

```bash
brew trust elastic/tap
brew install elastic/tap/elasticsearch-full   # 7.17.x
brew install rabbitmq
brew services start elasticsearch-full
brew services start rabbitmq
```

Apple Silicon 上 7.17 自带的 JDK 与机器学习原生组件是 x86_64，需两处调整：安装 `brew install openjdk@17`，并在 LaunchAgent plist（`~/Library/LaunchAgents/sh.brew.elasticsearch-full.plist`）的 `EnvironmentVariables` 中加入 `ES_JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home`；在 `/opt/homebrew/etc/elasticsearch/elasticsearch.yml` 追加 `xpack.ml.enabled: false`。注意 `brew services restart` 会重新生成 plist 并丢失 `ES_JAVA_HOME`，重启后需重新写入再 `launchctl kickstart -k`。RabbitMQ 默认 `amqp://guest:guest@127.0.0.1:5672/`，管理界面 <http://127.0.0.1:15672>。本机 ES 关闭了安全认证，仅限本地开发，生产必须启用认证与网络隔离。

**一致性与降级**：ES 是异步派生副本，商品写入到可搜索之间存在秒级延迟（中继间隔默认 2 秒）；库存、下单、支付等强一致判断始终在数据库事务内完成，不依赖 ES。ES 或 RabbitMQ 不可用时，搜索自动降级为数据库 `LIKE`，同步任务持续重试连接，恢复后通过对账补齐索引，商城功能不中断。

## 商品内容存储：MySQL + MongoDB + MinIO

商家发布商品时按内容类型分流存储，读取时再组装，兼顾事务一致性与高并发读：

- **写入**：基础信息（名称、价格、库存等）与**详情 HTML** 写入 MySQL `products` 表（`detail_html` 列）；**商品参数**（键值对）同时写入 MySQL `params_json` 列（事务内持久副本）与 **MongoDB** `product_params` 集合（以商品 id 为 `_id` 的读优化文档）；**图片**上传到 **MinIO**（S3/OSS 兼容对象存储），URL 仍为 `/media/<sha256>.<ext>`。
- **同步**：商品写入触发事务 outbox → RabbitMQ → 消费者，把名称、短介绍、描述、详情 HTML 的纯文本（`detail_text`）与参数文本（`params_text`）一并批量写入 Elasticsearch；搜索关键词可命中参数与详情内容。
- **读取**：搜索打 ES；详情页由后端组装 MySQL 基础信息 + `detail_html` 与 MongoDB 参数，结果放入既有 Redis `product` 缓存（商品/店铺写入触发器推进版本自动失效）。
- **安全**：详情 HTML 经 `app/html_sanitize.py` 白名单过滤（仅保留段落、列表、表格、图片等展示标签，丢弃 script/iframe/事件属性，图片 src 仅限 `/media/` 对象键），写入时即净化，前端直接渲染净化后的 HTML。
- **降级**：MongoDB 未配置或不可达时，参数回退读取 MySQL `params_json`；MinIO 未配置或不可达时，图片回退本地 `uploads/` 目录。两者故障都不影响发布与详情读取，恢复后自动切回。MongoDB 库名按数据库路径哈希隔离（`heima_params_<hash>`），测试互不污染。

依赖见 `requirements.txt` 的 `motor`、`minio`。本地可用 `brew install minio` 启动 MinIO（默认 `minioadmin/minioadmin`，端口 9000）；MongoDB 可用官方发行包启动 `mongod --bind_ip 127.0.0.1 --port 27017`。

## 使用 Docker 运行后端依赖

六个后端依赖服务已容器化（`docker-compose.yml`），Python 应用仍在宿主机运行，通过 `127.0.0.1` 的原生端口访问容器，因此 **`.env` 无需改动**。所有端口仅绑定 `127.0.0.1`。数据采用 **bind mount（目录映射）**，直接落在项目 `docker-data/<服务>` 主机目录，可在 Finder 浏览/备份（已加入 `.gitignore`）。

| 服务 | 镜像 | 宿主端口 | 主机数据目录 |
| --- | --- | --- | --- |
| MySQL | `mysql:8.4` | 3306 | `docker-data/mysql` |
| Redis | `redis:7-alpine` | 6379 | `docker-data/redis` |
| Elasticsearch | `elasticsearch:7.17.4` | 9200 | `docker-data/elasticsearch` |
| RabbitMQ | `rabbitmq:4-management` | 5672 / 15672 | `docker-data/rabbitmq` |
| MongoDB | `mongo:8.0` | 27017 | `docker-data/mongodb` |
| MinIO | `bitnamilegacy/minio:latest` | 9000 / 9001 | `docker-data/minio` |

**目录映射注意事项（macOS）：**

- 所有权：容器以非 root 用户运行（MySQL/Mongo/RabbitMQ uid 999、ES uid 1000、MinIO uid 1001）。Docker Desktop 的 VirtioFS 会做所有权映射，主机目录显示为当前用户所有，容器内按各自 uid 读写；迁移时已用辅助容器按卷根所有权 `chown`，MinIO 目录归一到 1001。
- MySQL 大小写敏感：macOS 主机文件系统**大小写不敏感**，MySQL 据此强制 `lower_case_table_names=2`，与原先在大小写敏感卷上初始化的数据字典（`0`）冲突而无法启动。解决办法是在映射目录上**重新初始化** MySQL 再导入 `backups/docker-migration/mysql_heima_mall.sql`，并跑一次应用 schema 初始化补回 `product_sync_outbox` 表与触发器（应用标识符均为小写，`lctn=2` 无影响）。
- 性能：bind mount 经 VirtioFS 转发，重 IO 服务（MySQL/ES）吞吐低于命名卷；如需极致性能可改回命名卷。数据持久性不受影响——删容器不丢数据，删 `docker-data/` 目录才会丢。

凭据不写入编排文件：MySQL 根密码经 `${MYSQL_PASSWORD}` 从 `.env` 注入，MinIO 经 `${OSS_ACCESS_KEY}/${OSS_SECRET_KEY}` 注入。RabbitMQ 通过 `docker/rabbitmq.conf` 的 `loopback_users = none` 允许 `guest` 经发布端口连接（应用使用 `durable` 队列，符合 RabbitMQ 4.x 默认策略）。

常用命令（`scripts/docker_services.sh`）：

```bash
scripts/docker_services.sh up        # 启动并查看状态
scripts/docker_services.sh status    # 健康检查
scripts/docker_services.sh reindex   # 从 MySQL 重建真实目录的 ES 索引
scripts/docker_services.sh down      # 停止容器（保留 docker-data/ 数据）
scripts/docker_services.sh rollback  # 打印回退到原生运行的重装+恢复指引
```

**从原生服务迁移数据（已执行一次）：** 先备份再停原生服务，再导入容器——

- MySQL：原生为 Homebrew `26.7.0`，其数据目录与官方容器镜像不兼容，故用 `mysqldump --databases heima_mall` 逻辑导出后导入 `mysql:8.4`（保留真实 `heima_mall` 数据）。
- MongoDB：用 `mongo:8.0` 容器内 `mongodump`（经 `host.docker.internal` 连原生）导出全部 `heima_params_*` 命名空间，再 `mongorestore` 入容器。
- MinIO：原生与容器镜像版本不同、对象为 erasure 编码（`xl.meta`），故经 S3 API 下载对象再上传入容器（避免原始数据目录格式不兼容）。
- Elasticsearch：容器启动后用 `scripts/docker_services.sh reindex` 从 MySQL 重建索引（保证与权威数据一致）。
- Redis / RabbitMQ：缓存与队列状态，使用全新卷，无需迁移。

备份留存于 `backups/docker-migration/`（已加入 `.gitignore`，含数据请勿提交）。**原生服务已卸载并清理其数据目录**，活数据现位于主机 `docker-data/`（bind mount），另保留 `backups/` 与项目 `data/`；`mongosh`/`mongodb-atlas-cli` 作为客户端保留。切换映射前的旧命名卷（`heima-mall_*_data`）仍作为冗余副本留存，确认无误后可用 `docker volume rm heima-mall_{mysql,redis,es,rabbitmq,mongo,minio}_data` 清理。如需回退到原生运行，`rollback` 子命令会打印重装与从备份恢复的指引。

## 配置与使用范围

- `DATABASE_BACKEND`：默认 `mysql`，可设为 `sqlite`。
- `MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_DATABASE` / `MYSQL_USER` / `MYSQL_PASSWORD`：本地 MySQL 连接信息；可用 `MYSQL_UNIX_SOCKET` 选择本地套接字。
- `DATABASE_PATH`：SQLite 模式下的文件位置，默认 `data/mall.db`。
- `DB_POOL_SIZE`：每个异步进程连接池上限，默认32，范围1–128，无额外溢出连接；获取连接最多等待5秒。
- `DB_OPERATION_TIMEOUT_SECONDS`：数据库操作和整个业务事务超时，默认10秒，范围大于0且不超过120秒。
- `MAX_IN_FLIGHT_REQUESTS` / `REQUEST_QUEUE_TIMEOUT_SECONDS`：每进程API处理准入与排队超时，默认128个/3秒。
- `REDIS_URL` / `REDIS_CACHE_TTL_SECONDS`：可选公共目录热点缓存及过期时间。
- `ELASTICSEARCH_URL` / `RABBITMQ_URL`：两者同时设置才启用 ES 搜索与异步同步（如 `http://127.0.0.1:9200`、`amqp://guest:guest@127.0.0.1:5672/`）；任一留空则回到数据库搜索。
- `ELASTICSEARCH_ALIAS`：索引别名前缀，默认 `heima_products`，实际别名按数据库路径哈希追加后缀。
- `SEARCH_SYNC_SECONDS` / `SEARCH_RECONCILE_SECONDS`：同步中继间隔（默认2秒，范围0–60）与对账间隔（默认300秒，范围0–86400）。
- `SEARCH_ES_TIMEOUT_SECONDS`：搜索请求 ES 的超时，默认2秒，范围0–30；超时即降级数据库查询。
- `MONGODB_URL` / `MONGODB_DATABASE`：商品参数读优化存储（如 `mongodb://127.0.0.1:27017`）；留空或不可达时回退 MySQL `params_json`。库名默认按数据库路径哈希隔离。
- `OSS_ENDPOINT` / `OSS_ACCESS_KEY` / `OSS_SECRET_KEY` / `OSS_BUCKET` / `OSS_SECURE`：MinIO 对象存储（如 `127.0.0.1:9000`、`minioadmin`、`minioadmin`、`heima-mall`）；留空或不可达时图片回退本地 `uploads/`。
- `uploads/`：SQLite 模式下位于数据库目录中，默认 MySQL 模式使用 `data/uploads/`；作为 MinIO 的本地回退与备份，请一并保存。
- `COOKIE_SECURE=1`：通过 HTTPS 提供服务时启用 Secure Cookie；本地 HTTP 开发保持默认值。
- 演示商品可以在 `app/seed.py` 中调整；种子数据不会覆盖已有数据库内的商品。需要重建演示数据时，应先停止服务、备份数据库，再移走 `data/mall.db` 及同名 WAL / SHM 文件后重启。

这是学习和开发起点，支付只更新演示订单状态，不会发生真实扣款，物流信息由管理员手动填写。标准库服务器用于本地开发；正式上线应使用 FastAPI 与生产 ASGI 服务，并根据业务接入真实支付、退款和待支付订单超时释放库存。登录限流目前保存在单个服务进程内。
