# 图片搜索测试用例

本文件记录图片搜索功能的黑盒与白盒测试范围。自动化用例使用外部关键词识别服务替身和确定性手工图像特征，不需要 API 密钥或模型权重，也不会消耗服务额度。

## 黑盒测试

| 编号 | 场景 | 预期 | 自动化用例 |
| --- | --- | --- | --- |
| B00 | 打开商城首页 | 显示图片搜索入口和选图控件，不提供相机直拍控件 | test_b00_storefront_offers_photo_selection_without_camera_capture |
| B01 | 查询图片搜索状态 | 返回启用状态和当前模式 | test_b01_status_reports_feature_state_without_exposing_configuration |
| B02 | 关键词兼容模式搜索 | 返回关键词和相关在售商品，最多 12 条 | test_b02_valid_photo_returns_live_catalog_matches |
| B02a | 本地视觉搜索 | 无 API key 时仍返回相似商品，并实时过滤库存、类目和店铺 | test_b02a_visual_search_needs_no_api_key_and_applies_live_business_filters |
| B03 | 未发现匹配商品 | 返回空 items 和 total=0 | test_b03_no_detected_product_returns_empty_result |
| B04 | 缺字段、额外字段、错误媒体类型、非法 JSON | 分别返回 422、415 或 400 | test_b04_invalid_request_shapes_return_client_errors |
| B05 | 缺少同源请求标头或跨站 Origin | 返回 403，业务处理器不执行 | test_b05_request_protection_blocks_cross_site_and_missing_header |
| B06 | 关键词识别服务不可用 | 返回脱敏的 503 重试提示 | test_b06_provider_failure_is_a_sanitized_service_error |
| B07 | 商品无库存或商品/店铺已下架 | 不返回不可购买商品 | test_b07_inactive_product_and_store_are_not_returned |
| B08 | 超过单客户端图片搜索频率 | 窗口内第 6 次返回 429 | test_b08_request_limit_is_visible_as_retryable_error |

## 白盒测试

| 编号 | 场景 | 预期 | 自动化用例 |
| --- | --- | --- | --- |
| W01 | 图片解码、方向修正、缩放、重编码 | 输出受限 JPEG 且移除 EXIF | test_w01_prepare_image_reencodes_pixels_and_strips_metadata |
| W02 | 解析模型关键词 | 去空格、去重；拒绝 HTML、超长、超量和不完整输出 | test_w02_keyword_parser_normalizes_and_rejects_untrusted_output |
| W03 | 校验视觉服务配置 | 拒绝 HTTP、内嵌凭证、查询参数和空模型 | test_w03_configuration_rejects_insecure_provider_endpoints |
| W04 | 识别异常和取消 | in_flight 计数在所有退出路径归零 | test_w04_reservation_releases_capacity_on_exception_and_cancellation |
| W05 | 检查上游请求契约 | JSON 模式、禁止存储、图片输入和禁止重定向 | test_w05_provider_contract_sets_json_mode_and_blocks_redirects |
| W06 | 上游连接、超时和响应过大 | 映射为脱敏 503 或受限 502 | test_w06_provider_transport_and_response_limits_map_to_safe_errors |
| W07 | 图片和请求边界 | 覆盖 JPEG/PNG/WebP、非法编码、尺寸、大小、Schema | tests/test_photo_search.py 中对应边界用例 |
| W08 | WSGI/FastAPI 两种适配器 | 大于默认 JSON 限制的合法请求仍能正确处理 | test_fastapi_adapter_accepts_photo_bodies_larger_than_default_json_limit |
| W09 | 无模型图像特征排序与目录增量同步 | 按感知哈希、颜色布局和边缘特征排序；删除、替换图片后更新特征并让其他 worker 看到新版本 | tests/test_visual_search.py |

## 本地视觉搜索 HTTP 回归测试

自动化文件：tests/test_photo_search_visual_integration.py。以下 19 个场景分别在真实 WSGI 和 FastAPI 入口执行，共 38 项测试。每项测试使用独立临时 SQLite 数据库、本地图片存储和真实特征索引；通过 Pillow 生成颜色、轮廓已知的杯形图片，验证 HTTP 请求到图像预处理、特征排序、SQL 过滤的完整链路。除主动模拟索引 I/O 故障的 V19 外，不替换图片检索实现。

| 编号 | 前置条件与操作 | 预期结果 |
| --- | --- | --- |
| V01 | 已构建本地索引，不设置外部 API 密钥；查询状态 | HTTP 200，enabled=true、mode=visual |
| V02 | 目录包含红、蓝、绿三张商品图；上传红色查询图 | 红色商品排第一，返回真实商品信息，keywords=[]、total=3、has_more=false |
| V03 | 分别上传 PNG、JPEG、WebP 查询图 | 三种格式均返回 HTTP 200，目标商品均排第一 |
| V04 | 按类目与店铺组合筛选，再使用不匹配组合 | 只返回同时符合两个条件的商品；不匹配组合返回空结果 |
| V05 | 索引构建后将最相似商品库存改为 0 | 搜索结果立即排除该商品 |
| V06 | 索引构建后下架最相似商品 | 搜索结果立即排除该商品 |
| V07 | 索引构建后暂停某店铺营业 | 该店所有商品均不出现在结果中 |
| V08 | 商品售罄后搜索，再补货后重新搜索 | 售罄时排除、补货后重新命中，无需手动重建索引 |
| V09 | 交换商品的图片引用后搜索 | 相似度排序与图片字段同步反映新商品图 |
| V10 | 新增黄色商品及其图片后查询黄色图片 | 自动同步目录，新商品排第一 |
| V11 | 分别准备恰好 12 件、13 件可匹配商品 | 均最多返回 12 件且无重复；has_more 分别为 false、true |
| V12 | 上传非法 Base64、非图片、截断 PNG、GIF | 各返回 HTTP 422；处理中请求计数恢复为 0 |
| V13 | 使用非法店铺 ID（含布尔值、字符串、溢出值）、非法类目和未知字段 | 各返回 HTTP 422，禁止隐式类型转换绕过校验 |
| V14 | 缺少自定义请求头、跨站 Origin，再提交正常请求 | 非法请求均为 HTTP 403；正常请求仍可搜索 |
| V15 | 同一客户端一分钟内连续提交 6 次 | 前 5 次成功，第 6 次 HTTP 429，资源计数无泄漏 |
| V16 | 删除索引文件，再写入损坏 JSON | 状态均安全降为 disabled；无关键词服务时搜索返回 HTTP 503 |
| V17 | 上传含私密 EXIF 标记、与目录原图不同的查询照片 | 搜索成功；上传目录和索引文件内容不变，商品图、库存和版本不变 |
| V18 | 将目录中所有商品下架后搜索 | HTTP 200，visual 模式空结果、total=0、has_more=false |
| V19 | 注入带私密路径的索引 I/O 异常，再恢复 | HTTP 503，响应无私密路径或堆栈；资源计数恢复，后续请求成功 |

V03、V12、V13、V14 使用子用例分别验证各格式和边界值；两个后端合计另有 34 个子用例。

## 执行命令

只运行新增的双后端回归：

    .venv/bin/python -m pytest -q tests/test_photo_search_visual_integration.py

运行图片搜索完整专项回归（包含前端控制器及评估工具测试）：

    .venv/bin/python -m pytest -q tests/test_photo_search*.py tests/test_visual_search*.py

统计两个图片搜索模块的语句与分支覆盖率（使用独立数据文件）：

    .venv/bin/python -m coverage run --branch --data-file=/tmp/heima-photo-search.coverage --source=app.photo_search,app.visual_search -m pytest -q tests/test_photo_search*.py tests/test_visual_search*.py
    .venv/bin/python -m coverage report --data-file=/tmp/heima-photo-search.coverage -m

黑盒用例只验证 HTTP 可观察行为；白盒用例检查图片净化、关键词解析、配置校验、上游请求构造、限流和资源释放。图像特征测试检查感知哈希、颜色与边缘排序及索引同步；检索准确率仍须使用真实商品标注集评估。
