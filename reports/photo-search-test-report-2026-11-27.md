# 图片搜索功能测试报告

测试日期：2026-11-27。

## 结果

| 检查范围 | 结果 |
| --- | --- |
| 新增本地视觉搜索 HTTP 回归 | 38 项通过，34 个子用例通过 |
| 图片搜索完整专项回归 | 93 项通过，79 个子用例通过，0 失败 |
| 项目全量回归 | 328 项通过，26 项跳过，104 个子用例通过，0 失败 |
| 新增用例的 unittest 入口 | 38 项通过 |
| 差异空白检查 | git diff --check 通过 |

专项和全量运行均出现 1 条来自 Starlette TestClient/httpx 的依赖弃用提示，不影响上述测试结果。本次没有修改业务代码。

## 新增测试交付

- 自动化测试：tests/test_photo_search_visual_integration.py。
- 测试规格：tests/PHOTO_SEARCH_TEST_CASES.md 中的 V01–V19。
- 19 个场景在 WSGI 和 FastAPI 两种入口各执行一次，共 38 项；其中图片格式、非法图片、字段边界、同源保护另有 34 个子用例。

覆盖本地索引状态、相似度排序、PNG/JPEG/WebP、类目及店铺过滤、售罄/下架/停业、补货和新增商品、图片替换后的索引同步、12/13 条结果边界、非法请求、客户端限流、索引损坏、异常脱敏与恢复，以及查询原图不写入商品上传目录或索引、商品记录不被修改。

测试使用临时 SQLite 数据库、真实本地特征索引、真实图片预处理和 Pillow 生成的确定性图片。V19 主动注入索引 I/O 故障，验证错误响应和后续恢复；正常检索链路不替换特征提取或检索实现。现有外部关键词识别兼容模式使用服务替身。

## 专项覆盖率

以下为 coverage.py 开启分支统计后报告的语句与分支综合覆盖率，测量范围仅为两个图片搜索模块。

| 模块 | 语句数 | 未覆盖语句 | 分支数 | 部分覆盖分支 | 综合覆盖率 |
| --- | ---: | ---: | ---: | ---: | ---: |
| app/photo_search.py | 253 | 17 | 92 | 8 | 93% |
| app/visual_search.py | 273 | 32 | 86 | 20 | 85% |
| 合计 | 526 | 49 | 178 | 28 | 89% |

覆盖率数据使用独立临时文件采集，未覆盖项目已有的 .coverage 文件。

## 复现命令

新增测试：

    .venv/bin/python -m pytest -q tests/test_photo_search_visual_integration.py
    .venv/bin/python -m unittest tests.test_photo_search_visual_integration -q

图片搜索专项及覆盖率：

    .venv/bin/python -m pytest -q tests/test_photo_search*.py tests/test_visual_search*.py
    .venv/bin/python -m coverage run --branch --data-file=/tmp/heima-photo-search.coverage --source=app.photo_search,app.visual_search -m pytest -q tests/test_photo_search*.py tests/test_visual_search*.py
    .venv/bin/python -m coverage report --data-file=/tmp/heima-photo-search.coverage -m

本次全量回归明确关闭外部服务集成开关：

    MYSQL_TESTS=0 MYSQL_ASYNC_TESTS=0 MONGODB_TESTS=0 SEARCH_INTEGRATION_TESTS=0 PRODUCT_SERVICES_TESTS=0 REDIS_TEST_URL= .venv/bin/python -m pytest -q

## 验证边界

上述结果证明所列功能和异常场景在测试环境中符合断言。生成的杯形图片用于验证颜色、轮廓和排序行为；真实商品照片的检索准确率、复杂背景及遮挡效果仍需人工标注集与评估脚本测量。本次未启用真实 MySQL、Redis、Elasticsearch、RabbitMQ、MongoDB、MinIO 集成测试，也未实际调用外部识图 API。
