# 黑盒 API 测试报告（stdlib 真实服务器）

测试时间：2026-11-19T07:39:58.409223+00:00。模式：real TCP server + temporary SQLite。
用例总数 70，通过 70，失败 0。

| 用例 | 域 | 方法 | 描述 | 结果 |
| --- | --- | --- | --- | --- |
| SYS-HEALTH | 系统 | 等价类-有效 | 健康检查返回200 | ✅ |
| SYS-SEC-HEADERS | 系统 | 错误推测 | 响应含安全头nosniff/DENY/CSP | ✅ |
| SEC-NO-XRW | 安全 | 错误推测 | 写请求缺少X-Requested-With被拒403 | ✅ |
| SEC-CROSS-ORIGIN | 安全 | 错误推测 | 跨域Origin写请求被拒绝 | ✅ |
| SEC-SQLI-SEARCH | 安全 | 错误推测 | 搜索SQL注入不报错且不泄露全表 | ✅ |
| SEC-PATH-TRAVERSAL | 安全 | 错误推测 | 静态目录路径穿越被拒绝 | ✅ |
| AUTH-CAPTCHA | 认证 | 等价类-有效 | 验证码返回id与PNG图片 | ✅ |
| AUTH-REG-BADPHONE | 认证 | 等价类-无效 | 非法手机号注册被拒 | ✅ |
| AUTH-REG-SHORTPWD | 认证 | 边界值 | 密码不足8位被拒 | ✅ |
| AUTH-REG-FIRST | 认证 | 等价类-有效 | 首次注册成功 | ✅ |
| AUTH-REG-DUP | 认证 | 错误推测 | 重复手机号注册被拒 | ✅ |
| AUTH-LOGIN-NOCAPTCHA | 认证 | 等价类-无效 | 缺少验证码登录被拒 | ✅ |
| AUTH-LOGIN-WRONGCAPTCHA | 认证 | 等价类-无效 | 错误验证码登录被拒 | ✅ |
| AUTH-LOGIN-WRONGPWD | 认证 | 等价类-无效 | 正确验证码错误密码被拒 | ✅ |
| AUTH-LOGIN-OK | 认证 | 等价类-有效 | 正确凭据登录成功 | ✅ |
| AUTH-ME | 认证 | 等价类-有效 | 登录后me返回当前账号 | ✅ |
| AUTH-ME-GUEST | 认证 | 等价类-无效 | 未登录me被拒 | ✅ |
| AUTH-LOGOUT | 认证 | 等价类-有效 | 退出登录成功 | ✅ |
| AUTH-ME-AFTER-LOGOUT | 认证 | 错误推测 | 退出后me失效 | ✅ |
| AUTH-REG-OK | 认证 | 等价类-有效 | 有效注册并自动登录 | ✅ |
| PROD-LIST | 商品 | 等价类-有效 | 商品列表返回items与total | ✅ |
| PROD-PAGE-0 | 商品 | 边界值 | page=0被拒 | ✅ |
| PROD-PAGESIZE-0 | 商品 | 边界值 | page_size=0被拒 | ✅ |
| PROD-PAGESIZE-MAX | 商品 | 边界值 | page_size=50有效 | ✅ |
| PROD-PAGESIZE-OVER | 商品 | 边界值 | page_size=51被拒 | ✅ |
| PROD-SORT-BAD | 商品 | 等价类-无效 | 非法排序被拒 | ✅ |
| PROD-SORT-PRICE | 商品 | 等价类-有效 | 价格升序排序有效 | ✅ |
| PROD-CATEGORY | 商品 | 等价类-有效 | 分类筛选有效 | ✅ |
| PROD-SEARCH-HIT | 商品 | 等价类-有效 | 关键词搜索命中 | ✅ |
| PROD-SEARCH-MISS | 商品 | 等价类-无效 | 无匹配关键词返回空 | ✅ |
| PROD-DETAIL-OK | 商品 | 等价类-有效 | 商品详情返回 | ✅ |
| PROD-DETAIL-404 | 商品 | 等价类-无效 | 不存在商品详情404 | ✅ |
| PROD-DETAIL-BADID | 商品 | 边界值 | 非法商品id被拒 | ✅ |
| PROD-CATEGORIES | 商品 | 等价类-有效 | 分类列表返回 | ✅ |
| CART-ADD-OK | 购物车 | 等价类-有效 | 添加商品到购物车 | ✅ |
| CART-ADD-404 | 购物车 | 等价类-无效 | 添加不存在商品被拒 | ✅ |
| CART-ADD-QTY0 | 购物车 | 边界值 | 数量0被拒 | ✅ |
| CART-ADD-OVERSTOCK | 购物车 | 边界值 | 超库存数量被拒 | ✅ |
| CART-GET | 购物车 | 等价类-有效 | 查看购物车 | ✅ |
| CART-UPDATE | 购物车 | 等价类-有效 | 更新购物车数量 | ✅ |
| CART-UPDATE-404 | 购物车 | 等价类-无效 | 更新未加入商品被拒 | ✅ |
| CART-GUEST | 购物车 | 等价类-无效 | 未登录查看购物车被拒 | ✅ |
| ORDER-CHECKOUT-OK | 订单 | 等价类-有效 | 单店结算创建订单 | ✅ |
| ORDER-IDEMPOTENT | 订单 | 错误推测 | 同一幂等键返回原订单 | ✅ |
| ORDER-IDEMPOTENT-CONFLICT | 订单 | 错误推测 | 同键不同请求体409 | ✅ |
| ORDER-DETAIL | 订单 | 等价类-有效 | 订单详情返回 | ✅ |
| ORDER-LIST | 订单 | 等价类-有效 | 订单列表返回 | ✅ |
| ORDER-PAY | 订单 | 等价类-有效 | 演示支付成功 | ✅ |
| ORDER-PAY-AGAIN | 订单 | 错误推测 | 已支付订单重复支付幂等返回 | ✅ |
| ORDER-CANCEL-PAID | 订单 | 错误推测 | 已支付订单取消被拒 | ✅ |
| ORDER-RECEIVE-UNSHIPPED | 订单 | 错误推测 | 未发货确认收货被拒 | ✅ |
| ORDER-CHECKOUT-EMPTY | 订单 | 边界值 | 空购物车结算被拒 | ✅ |
| ORDER-404 | 订单 | 等价类-无效 | 不存在订单404 | ✅ |
| COUPON-LIST | 优惠券 | 等价类-有效 | 优惠券列表返回 | ✅ |
| COUPON-CLAIM | 优惠券 | 等价类-有效 | 领取优惠券 | ✅ |
| COUPON-CLAIM-DUP | 优惠券 | 错误推测 | 重复领取幂等返回已领取 | ✅ |
| COUPON-CLAIM-404 | 优惠券 | 等价类-无效 | 领取不存在优惠券404 | ✅ |
| ADMIN-SETUP-STATUS | 后台 | 等价类-有效 | 初始化状态可查询 | ✅ |
| ADMIN-SETUP-OK | 后台 | 等价类-有效 | 本机首次创建终端管理员 | ✅ |
| ADMIN-SETUP-DUP | 后台 | 错误推测 | 二次初始化被拒 | ✅ |
| ADMIN-LOGIN | 后台 | 等价类-有效 | 管理员登录 | ✅ |
| ADMIN-DASHBOARD | 后台 | 等价类-有效 | 经营概览返回 | ✅ |
| ADMIN-PRODUCTS | 后台 | 等价类-有效 | 管理商品列表 | ✅ |
| ADMIN-PRODUCT-CREATE | 后台 | 等价类-有效 | 新增商品成功 | ✅ |
| ADMIN-PRODUCT-STATUS | 后台 | 等价类-有效 | 商品下架（乐观锁version） | ✅ |
| ADMIN-PRODUCT-INACTIVE-HIDDEN | 后台 | 错误推测 | 下架商品商城详情404 | ✅ |
| ADMIN-PERM-ISOLATION | 后台 | 错误推测 | 普通账号访问后台被拒 | ✅ |
| ISO-ORDER-CROSS | 隔离 | 错误推测 | 访问他人订单被拒 | ✅ |
| ISO-PAY-CROSS | 隔离 | 错误推测 | 支付他人订单被拒 | ✅ |
| ISO-CART-CROSS | 隔离 | 错误推测 | 购物车按账号隔离 | ✅ |

## 按域汇总

| 域 | 通过/总数 |
| --- | --- |
| 系统 | 2/2 |
| 安全 | 4/4 |
| 认证 | 14/14 |
| 商品 | 14/14 |
| 购物车 | 8/8 |
| 订单 | 11/11 |
| 优惠券 | 4/4 |
| 后台 | 10/10 |
| 隔离 | 3/3 |
