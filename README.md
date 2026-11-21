# junit-sum

零依赖 Python CLI：汇总 JUnit / Surefire / Failsafe / pytest 等导出的 xUnit XML 报告。看失败用例、错误、跳过、重试后通过（flaky），以及最慢的测试。不上传任何文件。

## 做什么

CI 失败时，`TEST-*.xml` 往往又长又碎：套件计数、用例、断言消息和堆栈混在一起，Agent 或人直接翻原始 XML 很容易漏。`junit-sum` 在本机解析报告，压缩成一页摘要。

它会：

- 读取单个 `<testsuite>`、`<testsuites>`，以及带 XML 命名空间的报告
- 扫描目录下的 `.xml`（可选 `-r` 递归）；忽略不是 JUnit 报告的 XML，并给出警告
- 统计通过 / 失败 / 错误 / 跳过，以及 Surefire 风格的 `rerunFailure` / `flakyFailure`（重试后通过会标成 flaky）
- 列出失败和错误的类名、用例名、异常类型、断言消息，以及截断后的堆栈
- 按耗时列出最慢用例
- 若报告声明的 `tests` 数量和实际 `testcase` 不一致，会给出警告
- `--json` 便于脚本处理；`--strict` 在有失败、错误或读入问题时以状态码 1 退出
- 支持 stdin（`-` 或管道）

## 要求

- Python 3.8+
- 仅标准库（`argparse` / `xml.etree.ElementTree` / `json` 等）

## 用法

```bash
# 查看仓库里的示例报告（虚构的 com.example 用例）
python3 junit_sum.py examples/order-tests.xml

# 汇总目录中的全部 JUnit XML
python3 junit_sum.py examples/

# 递归扫描 Surefire 目录
python3 junit_sum.py -r target/surefire-reports --strict

# 只要失败和错误，堆栈多留几行
python3 junit_sum.py report.xml --failed-only --stack 12

# 管道 / stdin
cat examples/namespaced.xml | python3 junit_sum.py
python3 junit_sum.py - < examples/pytest-sample.xml

# 机器可读 JSON
python3 junit_sum.py examples/pytest-sample.xml --json
```

`examples/` 里是虚构的演示报告（包名 `com.example.shop`、`tests.test_cart`），不是真实项目的测试结果，也不包含任何真实用户或业务数据。

## 示例

`python3 junit_sum.py examples/order-tests.xml --stack 3`：

```
Source:     examples/order-tests.xml
Suites:     1
Tests:      6   passed 3   failed 1   errors 1   skipped 1   flaky 1
Time:       0.447s

Failures (1)
  com.example.shop.OrderServiceTest.rejectsNegativeQuantity  (0.021s)
    org.junit.ComparisonFailure: expected:<ACCEPTED> but was:<REJECTED>
    	at com.example.shop.OrderServiceTest.rejectsNegativeQuantity(OrderServiceTest.java:41)
    	at java.base/jdk.internal.reflect.NativeMethodAccessorImpl.invoke0(Native Method)
    ...

Errors (1)
  com.example.shop.OrderServiceTest.throwsOnMissingSku  (0.018s)
    java.lang.IllegalArgumentException: sku is required
    	at com.example.shop.OrderService.place(OrderService.java:88)
    	at com.example.shop.OrderServiceTest.throwsOnMissingSku(OrderServiceTest.java:67)

Skipped (1)
  com.example.shop.OrderServiceTest.skipsWhenInventoryOffline  (0.001s)
    inventory service flag is off on this runner

Flaky (passed on retry) (1)
  com.example.shop.OrderServiceTest.retryThenPass  (0.040s)
    earlier: java.net.SocketTimeoutException transient timeout talking to catalog
    passed on retry

Slowest
      0.353s  com.example.shop.CatalogIT.loadsFullCatalog
      0.040s  com.example.shop.OrderServiceTest.retryThenPass  [flaky]
      0.021s  com.example.shop.OrderServiceTest.rejectsNegativeQuantity  [failed]
      0.018s  com.example.shop.OrderServiceTest.throwsOnMissingSku  [error]
      0.014s  com.example.shop.OrderServiceTest.acceptsPositiveQuantity
```

## 限制

- 只认 xUnit / JUnit XML，不解析 Surefire 纯文本日志、Allure、HTML 报告或 TAP
- 耗时按各 `testcase` 的 `time` 相加；若用例没有时间，再回退到套件声明的总时间
- 堆栈默认只保留 5 行（`--stack` 可改）；不会试图把失败自动分类成「同类 bug」
- 同一个用例多次重试后仍失败，只按最终失败统计，同时标 flaky
- 单文件超过 50 MB 会跳过

## 后续可改进

- 按异常类型 / 断言消息做失败归并
- 对比两次报告，标出新增失败和突然变慢的用例
- 识别 Gradle / TestNG 的更多扩展字段（attachments、hostname 分组）

## 许可

MIT。见 [LICENSE](LICENSE)。
