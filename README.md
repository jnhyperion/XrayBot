Xray-Bot
=============
Synchronize atlassian xray test case tickets with your test code and upload the test results.

Install
-------
``` sh
$ pip install xray-bot
```

Example
-------
``` python
from xraybot import XrayBot, TestEntity, TestResultEntity, XrayResultType

xray_bot = XrayBot("http://jira_server", "username", "pwd", "project_key")

xray_tests = xray_bot.get_xray_tests()

local_tests = [
    TestEntity(
        unique_identifier="com.foo.bar.TestClass#testFoo",
        summary="foo",
        description="desc",
        req_key="REQ-100",
    ),
    TestEntity(
        unique_identifier="com.foo.bar.TestClass#testBar",
        summary="Bar",
        description="desc",
        req_key="REQ-101",
    ),
]
xray_bot.sync_tests(local_tests)

test_results = [
    TestResultEntity(key="KEY-1", result=XrayResultType.PASS),
    TestResultEntity(key="KEY-2", result=XrayResultType.FAIL),
]
xray_bot.upload_automation_results("test_plan", "test_execution", test_results)
```
Development
-------
``` sh
$ pip install -e ".[dev]"
# test
$ invoke test
# lint
$ invoke lint
# reformat code
$ invoke reformat-code
# install
$ invoke install
```
