# Photo Search Test Cases

本文件记录照片识别商品功能的黑盒与白盒测试范围。外部视觉模型在自动化测试中使用边界替身，不需要真实密钥，也不会消耗服务额度。

## 黑盒测试

| 编号 | 场景 | 预期 | 自动化用例 |
| --- | --- | --- | --- |
| B00 | 打开商城首页 | 显示照片识别入口和选图控件，不提供相机直拍控件 | test_b00_storefront_offers_photo_selection_without_camera_capture |
| B01 | 查询识别服务状态 | 返回 200，仅暴露 enabled 状态 | test_b01_status_reports_feature_state_without_exposing_configuration |
| B02 | 上传有效商品照片并识别到商品 | 返回关键词、在售商品，最多 12 条 | test_b02_valid_photo_returns_live_catalog_matches |
| B03 | 未识别到商品 | 返回空 keywords、items 和 total=0 | test_b03_no_detected_product_returns_empty_result |
| B04 | 缺字段、额外字段、错误媒体类型、非法 JSON | 分别返回 422、415 或 400 | test_b04_invalid_request_shapes_return_client_errors |
| B05 | 缺少同源请求标头或跨站 Origin | 返回 403，业务处理器不执行 | test_b05_request_protection_blocks_cross_site_and_missing_header |
| B06 | 视觉服务不可用 | 返回脱敏的 503 重试提示 | test_b06_provider_failure_is_a_sanitized_service_error |
| B07 | 商品或店铺已下架 | 不返回失效商品，total 为 0 | test_b07_inactive_product_and_store_are_not_returned |
| B08 | 超过单客户端识别频率 | 窗口内第 6 次返回 429 | test_b08_request_limit_is_visible_as_retryable_error |

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

## 执行命令

    .venv/bin/python -m pytest -q tests/test_photo_search.py tests/test_photo_search_blackbox.py tests/test_photo_search_whitebox.py
    .venv/bin/python -m coverage erase
    .venv/bin/python -m coverage run -m pytest -q tests/test_photo_search.py tests/test_photo_search_blackbox.py tests/test_photo_search_whitebox.py
    .venv/bin/python -m coverage report -m app/photo_search.py

黑盒用例只验证 HTTP 可观察行为；白盒用例检查图片净化、关键词解析、配置校验、上游请求构造、限流和资源释放。
