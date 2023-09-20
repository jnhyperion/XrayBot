import os
import json
import pytest
from copy import deepcopy
from unittest.mock import call, ANY
from xraybot import XrayBot, TestEntity, TestResultEntity, XrayResultType


tests_dir = os.path.abspath(os.path.dirname(__file__))
root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


@pytest.fixture(autouse=True)
def setup():
    yield


mock_url = "http://test.com"
mock_username = "username"
mock_pwd = "pwd"
mock_project_key = "DEMO"
mock_get_cf_request = f"{mock_url}/rest/api/2/field"
mock_get_folder_request = (
    f"{mock_url}/rest/raven/1.0/api/testrepository/{mock_project_key}/folders"
)
mock_search_request = (
    f"{mock_url}/rest/api/2/search?startAt=0&maxResults=-1&"
    f"fields=summary%2Cdescription%2Cissuelinks%2Ccustomfield_100&"
    f"jql=project+%3D+%22{mock_project_key}%22+and+type+%3D+%22Test%22+"
    f"and+reporter+%3D+%22{mock_username}%22+"
    f"and+status+%21%3D+%22Obsolete%22+and+issue+in+testRepositoryFolderTests%28%22{mock_project_key}%22%2C+"
    f"%22Automation+Test%22%29+and+%22Test+Type%22+%3D+%22Generic%22"
)
mock_xray_tests = [
    TestEntity(
        key="DEMO-11",
        req_key="REQ-120",
        summary="test_error",
        description="tests the multi error",
        unique_identifier="tests/my-directory/test_demo.py::test_error",
    ),
    TestEntity(
        key="DEMO-10",
        req_key="REQ-120",
        summary="test_invalid_email_format",
        description="Summary: Check the invalid email format\nExpectation: the warning is same as the design\n"
        "1. input a invalid email format\n2. click the login button",
        unique_identifier="tests/my-directory/test_demo.py::test_invalid_email_format",
    ),
    TestEntity(
        key="DEMO-9",
        req_key="REQ-120",
        summary="test_login_app_invalid_account",
        description="Summary: Use the invalid account to login the app\n"
        "Expectation: the warning is same as the design\n1. input a invalid account and password\n"
        "2. click the login button",
        unique_identifier="tests/my-directory/test_demo.py::test_login_app_invalid_account",
    ),
]
cpy_mock_xray_tests = deepcopy(mock_xray_tests)
to_be_appended1 = TestEntity(
    summary="Foo",
    description="Foo desc",
    req_key="REQ-100",
    unique_identifier="tests/my-directory/test_demo.py::Foo",
)
to_be_appended2 = TestEntity(
    summary="Bar",
    description="Bar desc",
    req_key="REQ-101",
    unique_identifier="tests/my-directory/test_demo.py::Bar",
)

to_be_updated = cpy_mock_xray_tests[0]
to_be_updated.description = "updated desc"
to_be_deleted = cpy_mock_xray_tests[2]
local_test2 = deepcopy(to_be_updated)
local_test2.key = None
local_test3 = deepcopy(cpy_mock_xray_tests[1])
local_test3.key = None
local_tests = [to_be_appended1, to_be_appended2, local_test2, local_test3]


def _get_response(name) -> dict:
    with open(os.path.join(tests_dir, "response", f"{name}.json")) as f:
        return json.load(f)


@pytest.fixture(autouse=True)
def setup(requests_mock):
    to_be_appended1.key = None
    to_be_appended2.key = None
    local_test2.key = None
    local_test3.key = None
    requests_mock.get(mock_get_cf_request, json=_get_response("custom_fields"))
    requests_mock.get(mock_get_folder_request, json=_get_response("folder"))
    yield


def test_get_xray_tests(requests_mock):
    requests_mock.get(mock_search_request, json=_get_response("search"))
    xray_bot = XrayBot(mock_url, mock_username, mock_pwd, mock_project_key)
    xray_tests = xray_bot.get_xray_tests()
    assert xray_tests == mock_xray_tests


def test_get_xray_tests_with_empty_description(requests_mock):
    requests_mock.get(mock_search_request, json=_get_response("search_desc_null"))
    xray_bot = XrayBot(mock_url, mock_username, mock_pwd, mock_project_key)
    xray_tests = xray_bot.get_xray_tests()
    assert xray_tests == [
        TestEntity(
            key="DEMO-11",
            req_key="REQ-120",
            summary="test_error",
            description="",
            unique_identifier="tests/my-directory/test_demo.py::test_error",
        )
    ]


def test_get_xray_tests_with_cf_value_str(requests_mock):
    requests_mock.get(
        mock_search_request + "+and+%22Test+Scope%22+%3D+%22BAT%22",
        json=_get_response("search"),
    )
    xray_bot = XrayBot(mock_url, mock_username, mock_pwd, mock_project_key)
    xray_bot.configure_custom_field("Test Scope", "BAT")
    xray_tests = xray_bot.get_xray_tests()
    assert xray_tests == mock_xray_tests


def test_get_xray_tests_with_cf_value_list(requests_mock):
    requests_mock.get(
        mock_search_request + "+and+%22Test+Case+Platform%22+in+%28%22Android%22%29",
        json=_get_response("search"),
    )
    xray_bot = XrayBot(mock_url, mock_username, mock_pwd, mock_project_key)
    xray_bot.configure_custom_field("Test Case Platform", ["Android"])
    xray_tests = xray_bot.get_xray_tests()
    assert xray_tests == mock_xray_tests


def test_get_xray_tests_with_cf_value_str_list(requests_mock):
    requests_mock.get(
        mock_search_request.replace("Generic", "Automated")
        + "+and+%22Test+Case+Platform%22+in+%28%22Android%22%29",
        json=_get_response("search"),
    )
    xray_bot = XrayBot(
        mock_url,
        mock_username,
        mock_pwd,
        mock_project_key,
    )
    xray_bot.configure_custom_field("Test Type", "Automated")
    xray_bot.configure_custom_field("Test Case Platform", ["Android"])
    xray_tests = xray_bot.get_xray_tests()
    assert xray_tests == mock_xray_tests


def test_get_test_diff(requests_mock):
    xray_bot = XrayBot(mock_url, mock_username, mock_pwd, mock_project_key)
    requests_mock.get(mock_search_request, json=_get_response("search"))
    xray_tests = xray_bot.get_xray_tests()
    (
        _to_be_deleted,
        _to_be_appended,
        _to_be_updated,
    ) = xray_bot._get_non_marked_tests_diff(xray_tests, local_tests)
    assert _to_be_deleted == [to_be_deleted]
    assert _to_be_appended == [to_be_appended1, to_be_appended2]
    assert _to_be_updated == [to_be_updated]


def test_xray_sync(mocker):
    xray_bot = XrayBot(mock_url, mock_username, mock_pwd, mock_project_key)

    def mock_side_effect(worker_wrapper, func_list, *iterables):
        results = []
        for idx, func in enumerate(func_list):
            results.append(worker_wrapper(func, *list(zip(*iterables))[idx]))
        return results

    jql_side_effect_counter = 0

    def jql_side_effect(*_, **__):
        nonlocal jql_side_effect_counter
        jql_side_effect_counter = jql_side_effect_counter + 1
        if jql_side_effect_counter == 1:
            return _get_response("search")
        else:
            return _get_response("search_after")

    mock_process_pool = mocker.patch("xraybot._worker.ProcessPoolExecutor.map")
    mock_process_pool.side_effect = mock_side_effect
    mocker.patch("xraybot._context.XrayBotContext.xray")
    mock_jira = mocker.patch("xraybot._context.XrayBotContext.jira")
    mock_jira.username = "username"
    mock_jira.jql.side_effect = jql_side_effect
    mock_jira.get_all_custom_fields.return_value = _get_response("custom_fields")
    xray_bot.sync_tests(local_tests)
    # create 2 cases
    assert mock_jira.create_issue.call_args_list == [
        call(
            {
                "issuetype": {"name": "Test"},
                "project": {"key": "DEMO"},
                "description": "Foo desc",
                "summary": "Foo",
                "assignee": {"name": "username"},
                "customfield_100": "tests/my-directory/test_demo.py::Foo",
                "customfield_15095": {"value": "Generic"},
            }
        ),
        call(
            {
                "issuetype": {"name": "Test"},
                "project": {"key": "DEMO"},
                "description": "Bar desc",
                "summary": "Bar",
                "assignee": {"name": "username"},
                "customfield_100": "tests/my-directory/test_demo.py::Bar",
                "customfield_15095": {"value": "Generic"},
            }
        ),
    ]
    # set one deleted cases and finalize 2 new cases
    assert mock_jira.set_issue_status.call_args_list == [
        call("DEMO-9", "Obsolete"),
        call(ANY, "Ready for Review"),
        call(ANY, "In Review"),
        call(ANY, "Finalized"),
        call(ANY, "Ready for Review"),
        call(ANY, "In Review"),
        call(ANY, "Finalized"),
    ]
    # update case
    assert mock_jira.update_issue_field.call_args_list == [
        call(
            key="DEMO-11",
            fields={"summary": "test_error", "description": "updated desc"},
        )
    ]


def test_xray_sync_with_cf_value_str(mocker):
    xray_bot = XrayBot(mock_url, mock_username, mock_pwd, mock_project_key)
    xray_bot.configure_custom_field("Test Type", "Automated")

    def mock_side_effect(worker_wrapper, func_list, *iterables):
        results = []
        for idx, func in enumerate(func_list):
            results.append(worker_wrapper(func, *list(zip(*iterables))[idx]))
        return results

    jql_side_effect_counter = 0

    def jql_side_effect(*_, **__):
        nonlocal jql_side_effect_counter
        jql_side_effect_counter = jql_side_effect_counter + 1
        if jql_side_effect_counter == 1:
            return _get_response("search")
        else:
            return _get_response("search_after")

    mock_process_pool = mocker.patch("xraybot._worker.ProcessPoolExecutor.map")
    mock_process_pool.side_effect = mock_side_effect
    mocker.patch("xraybot._context.XrayBotContext.xray")
    mock_jira = mocker.patch("xraybot._context.XrayBotContext.jira")
    mock_jira.username = "username"
    mock_jira.jql.side_effect = jql_side_effect
    mock_jira.get_all_custom_fields.return_value = _get_response("custom_fields")
    xray_bot.sync_tests(local_tests)
    # create 2 cases
    assert mock_jira.create_issue.call_args_list == [
        call(
            {
                "issuetype": {"name": "Test"},
                "project": {"key": "DEMO"},
                "description": "Foo desc",
                "summary": "Foo",
                "assignee": {"name": "username"},
                "customfield_100": "tests/my-directory/test_demo.py::Foo",
                "customfield_15095": {"value": "Automated"},
            }
        ),
        call(
            {
                "issuetype": {"name": "Test"},
                "project": {"key": "DEMO"},
                "description": "Bar desc",
                "summary": "Bar",
                "assignee": {"name": "username"},
                "customfield_100": "tests/my-directory/test_demo.py::Bar",
                "customfield_15095": {"value": "Automated"},
            }
        ),
    ]
    # set one deleted cases and finalize 2 new cases
    assert mock_jira.set_issue_status.call_args_list == [
        call("DEMO-9", "Obsolete"),
        call(ANY, "Ready for Review"),
        call(ANY, "In Review"),
        call(ANY, "Finalized"),
        call(ANY, "Ready for Review"),
        call(ANY, "In Review"),
        call(ANY, "Finalized"),
    ]
    # update case
    assert mock_jira.update_issue_field.call_args_list == [
        call(
            key="DEMO-11",
            fields={"summary": "test_error", "description": "updated desc"},
        )
    ]


def test_xray_sync_with_cf_value_list(mocker):
    xray_bot = XrayBot(mock_url, mock_username, mock_pwd, mock_project_key)
    xray_bot.configure_custom_field("Test Case Platform", ["Android"])

    def mock_side_effect(worker_wrapper, func_list, *iterables):
        results = []
        for idx, func in enumerate(func_list):
            results.append(worker_wrapper(func, *list(zip(*iterables))[idx]))
        return results

    jql_side_effect_counter = 0

    def jql_side_effect(*_, **__):
        nonlocal jql_side_effect_counter
        jql_side_effect_counter = jql_side_effect_counter + 1
        if jql_side_effect_counter == 1:
            return _get_response("search")
        else:
            return _get_response("search_after")

    mock_process_pool = mocker.patch("xraybot._worker.ProcessPoolExecutor.map")
    mock_process_pool.side_effect = mock_side_effect
    mocker.patch("xraybot._context.XrayBotContext.xray")
    mock_jira = mocker.patch("xraybot._context.XrayBotContext.jira")
    mock_jira.username = "username"
    mock_jira.jql.side_effect = jql_side_effect
    mock_jira.get_all_custom_fields.return_value = _get_response("custom_fields")
    xray_bot.sync_tests(local_tests)
    # create 2 cases
    assert mock_jira.create_issue.call_args_list == [
        call(
            {
                "issuetype": {"name": "Test"},
                "project": {"key": "DEMO"},
                "description": "Foo desc",
                "summary": "Foo",
                "assignee": {"name": "username"},
                "customfield_100": "tests/my-directory/test_demo.py::Foo",
                "customfield_15095": {"value": "Generic"},
                "customfield_15183": [{"value": "Android"}],
            }
        ),
        call(
            {
                "issuetype": {"name": "Test"},
                "project": {"key": "DEMO"},
                "description": "Bar desc",
                "summary": "Bar",
                "assignee": {"name": "username"},
                "customfield_100": "tests/my-directory/test_demo.py::Bar",
                "customfield_15095": {"value": "Generic"},
                "customfield_15183": [{"value": "Android"}],
            }
        ),
    ]
    # set one deleted cases and finalize 2 new cases
    assert mock_jira.set_issue_status.call_args_list == [
        call("DEMO-9", "Obsolete"),
        call(ANY, "Ready for Review"),
        call(ANY, "In Review"),
        call(ANY, "Finalized"),
        call(ANY, "Ready for Review"),
        call(ANY, "In Review"),
        call(ANY, "Finalized"),
    ]
    # update case
    assert mock_jira.update_issue_field.call_args_list == [
        call(
            key="DEMO-11",
            fields={"summary": "test_error", "description": "updated desc"},
        )
    ]


def test_xray_sync_with_cf_value_str_list(mocker):
    xray_bot = XrayBot(
        mock_url,
        mock_username,
        mock_pwd,
        mock_project_key,
    )
    xray_bot.configure_custom_field("Test Type", "Automated")
    xray_bot.configure_custom_field("Test Case Platform", ["Android"])

    def mock_side_effect(worker_wrapper, func_list, *iterables):
        results = []
        for idx, func in enumerate(func_list):
            results.append(worker_wrapper(func, *list(zip(*iterables))[idx]))
        return results

    jql_side_effect_counter = 0

    def jql_side_effect(*_, **__):
        nonlocal jql_side_effect_counter
        jql_side_effect_counter = jql_side_effect_counter + 1
        if jql_side_effect_counter == 1:
            return _get_response("search")
        else:
            return _get_response("search_after")

    mock_process_pool = mocker.patch("xraybot._worker.ProcessPoolExecutor.map")
    mock_process_pool.side_effect = mock_side_effect
    mocker.patch("xraybot._context.XrayBotContext.xray")
    mock_jira = mocker.patch("xraybot._context.XrayBotContext.jira")
    mock_jira.username = "username"
    mock_jira.jql.side_effect = jql_side_effect
    mock_jira.get_all_custom_fields.return_value = _get_response("custom_fields")
    xray_bot.sync_tests(local_tests)
    # create 2 cases
    assert mock_jira.create_issue.call_args_list == [
        call(
            {
                "issuetype": {"name": "Test"},
                "project": {"key": "DEMO"},
                "description": "Foo desc",
                "summary": "Foo",
                "assignee": {"name": "username"},
                "customfield_100": "tests/my-directory/test_demo.py::Foo",
                "customfield_15095": {"value": "Automated"},
                "customfield_15183": [{"value": "Android"}],
            }
        ),
        call(
            {
                "issuetype": {"name": "Test"},
                "project": {"key": "DEMO"},
                "description": "Bar desc",
                "summary": "Bar",
                "assignee": {"name": "username"},
                "customfield_100": "tests/my-directory/test_demo.py::Bar",
                "customfield_15095": {"value": "Automated"},
                "customfield_15183": [{"value": "Android"}],
            }
        ),
    ]
    # set one deleted cases and finalize 2 new cases
    assert mock_jira.set_issue_status.call_args_list == [
        call("DEMO-9", "Obsolete"),
        call(ANY, "Ready for Review"),
        call(ANY, "In Review"),
        call(ANY, "Finalized"),
        call(ANY, "Ready for Review"),
        call(ANY, "In Review"),
        call(ANY, "Finalized"),
    ]
    # update case
    assert mock_jira.update_issue_field.call_args_list == [
        call(
            key="DEMO-11",
            fields={"summary": "test_error", "description": "updated desc"},
        )
    ]


def test_upload_results(mocker):
    xray_bot = XrayBot(mock_url, mock_username, mock_pwd, mock_project_key)

    test_plan_id = 100
    test_exec_id = 101
    test_run_id = 10001

    def create_issue_side_effect(fields):
        if fields["issuetype"] == {"name": "Test Plan"}:
            return {"key": test_plan_id}
        elif fields["issuetype"] == {"name": "Test Execution"}:
            return {"key": test_exec_id}
        else:
            raise AssertionError(f"Unexpected create issue fields {fields}")

    def mock_side_effect(worker_wrapper, func_list, *iterables):
        results = []
        for idx, func in enumerate(func_list):
            results.append(worker_wrapper(func, *list(zip(*iterables))[idx]))
        return results

    mock_process_pool = mocker.patch("xraybot._worker.ProcessPoolExecutor.map")
    mock_process_pool.side_effect = mock_side_effect
    mock_xray = mocker.patch("xraybot._context.XrayBotContext.xray")
    mock_jira = mocker.patch("xraybot._context.XrayBotContext.jira")
    mock_jira.username = "username"
    mock_jira.jql.return_value = _get_response("search")
    mock_jira.create_issue.side_effect = create_issue_side_effect
    mock_jira.get_all_custom_fields.return_value = _get_response("custom_fields")
    mock_xray.get_test_runs.return_value = [
        {"testExecKey": test_exec_id, "id": test_run_id}
    ]
    test_results = [
        TestResultEntity(key="DEMO-11", result=XrayResultType.TODO),
        TestResultEntity(key="DEMO-10", result=XrayResultType.PASS),
        TestResultEntity(key="DEMO-9", result=XrayResultType.FAIL),
    ]
    xray_bot.upload_test_results("test_plan", "test_exec", test_results)
    assert mock_jira.create_issue.call_args_list == [
        call(
            {
                "issuetype": {"name": "Test Plan"},
                "project": {"key": "DEMO"},
                "summary": "test_plan",
                "assignee": {"name": "username"},
            }
        ),
        call(
            {
                "issuetype": {"name": "Test Execution"},
                "project": {"key": "DEMO"},
                "summary": "test_exec",
                "assignee": {"name": "username"},
            }
        ),
    ]
    assert mock_xray.update_test_plan.call_args_list == [
        call(test_plan_id, add=["DEMO-11"]),
        call(test_plan_id, add=["DEMO-10"]),
        call(test_plan_id, add=["DEMO-9"]),
    ]
    assert mock_xray.update_test_execution.call_args_list == [
        call(test_exec_id, add=["DEMO-11"]),
        call(test_exec_id, add=["DEMO-10"]),
        call(test_exec_id, add=["DEMO-9"]),
    ]
    assert mock_jira.update_issue_field.call_args_list == [
        call(101, fields={None: [100]})
    ]
    assert mock_xray.update_test_run_status.call_args_list == [
        call(test_run_id, "TODO"),
        call(test_run_id, "PASS"),
        call(test_run_id, "FAIL"),
    ]


def test_xray_sync_with_external_marked_tests(mocker):
    xray_bot = XrayBot(mock_url, mock_username, mock_pwd, mock_project_key)

    def mock_side_effect(worker_wrapper, func_list, *iterables):
        results = []
        for idx, func in enumerate(func_list):
            results.append(worker_wrapper(func, *list(zip(*iterables))[idx]))
        return results

    def get_issue_side_effect(key, *_, **__):
        if key == "DEMO-999":
            return _get_response("get_issue")
        else:
            return mocker.MagicMock()

    jql_side_effect_counter = 0

    def jql_side_effect(*_, **__):
        nonlocal jql_side_effect_counter
        jql_side_effect_counter = jql_side_effect_counter + 1
        if jql_side_effect_counter == 1:
            return _get_response("search")
        else:
            return _get_response("search_after_with_marked")

    get_issue_status_counter = 0

    def get_issue_status_side_effect(key):
        nonlocal get_issue_status_counter
        get_issue_status_counter = get_issue_status_counter + 1
        if key == "DEMO-999":
            if get_issue_status_counter == 1:
                return "In-Draft"
            else:
                return "Finalized"
        else:
            return mocker.MagicMock()

    mock_process_pool = mocker.patch("xraybot._worker.ProcessPoolExecutor.map")
    mock_process_pool.side_effect = mock_side_effect
    mocker.patch("xraybot._context.XrayBotContext.xray")
    mock_jira = mocker.patch("xraybot._context.XrayBotContext.jira")
    mock_jira.username = "username"
    mock_jira.jql.side_effect = jql_side_effect
    mock_jira.get_issue.side_effect = get_issue_side_effect
    mock_jira.get_issue_status.side_effect = get_issue_status_side_effect
    mock_jira.get_all_custom_fields.return_value = _get_response("custom_fields")
    marked_test = TestEntity(
        key="DEMO-999",
        summary="Marked Foo",
        description="Marked Foo desc",
        req_key="REQ-109",
        unique_identifier="tests/my-directory/test_marked.py::Foo_999",
    )

    xray_bot.sync_tests([*local_tests, marked_test])
    # create 2 cases
    assert mock_jira.create_issue.call_args_list == [
        call(
            {
                "issuetype": {"name": "Test"},
                "project": {"key": "DEMO"},
                "description": "Foo desc",
                "summary": "Foo",
                "assignee": {"name": "username"},
                "customfield_100": "tests/my-directory/test_demo.py::Foo",
                "customfield_15095": {"value": "Generic"},
            }
        ),
        call(
            {
                "issuetype": {"name": "Test"},
                "project": {"key": "DEMO"},
                "description": "Bar desc",
                "summary": "Bar",
                "assignee": {"name": "username"},
                "customfield_100": "tests/my-directory/test_demo.py::Bar",
                "customfield_15095": {"value": "Generic"},
            }
        ),
    ]
    # Obsolete one deleted test and finalize 2 new tests, 1 marked test
    assert mock_jira.set_issue_status.call_args_list == [
        call("DEMO-999", "In-Draft"),
        call("DEMO-999", "Ready for Review"),
        call("DEMO-999", "In Review"),
        call("DEMO-999", "Finalized"),
        call("DEMO-9", "Obsolete"),
        call(ANY, "Ready for Review"),
        call(ANY, "In Review"),
        call(ANY, "Finalized"),
        call(ANY, "Ready for Review"),
        call(ANY, "In Review"),
        call(ANY, "Finalized"),
    ]
    # update case
    assert mock_jira.update_issue_field.call_args_list == [
        call(
            key="DEMO-999",
            fields={
                "description": "Marked Foo desc",
                "summary": "Marked Foo",
                "assignee": {"name": "username"},
                "reporter": {"name": "username"},
                "customfield_100": "tests/my-directory/test_marked.py::Foo_999",
                "customfield_15095": {"value": "Generic"},
                "labels": [],
            },
        ),
        call(
            key="DEMO-11",
            fields={"summary": "test_error", "description": "updated desc"},
        ),
    ]


def test_xray_sync_with_lower_local_test_key(mocker):
    xray_bot = XrayBot(mock_url, mock_username, mock_pwd, mock_project_key)

    def mock_side_effect(worker_wrapper, func_list, *iterables):
        results = []
        for idx, func in enumerate(func_list):
            results.append(worker_wrapper(func, *list(zip(*iterables))[idx]))
        return results

    def get_issue_side_effect(key, *_, **__):
        if key == "DEMO-999":
            return _get_response("get_issue")
        else:
            return mocker.MagicMock()

    jql_side_effect_counter = 0

    def jql_side_effect(*_, **__):
        nonlocal jql_side_effect_counter
        jql_side_effect_counter = jql_side_effect_counter + 1
        if jql_side_effect_counter == 1:
            return _get_response("search")
        else:
            return _get_response("search_after_with_marked")

    get_issue_status_counter = 0

    def get_issue_status_side_effect(key):
        nonlocal get_issue_status_counter
        get_issue_status_counter = get_issue_status_counter + 1
        if key == "DEMO-999":
            if get_issue_status_counter == 1:
                return "In-Draft"
            else:
                return "Finalized"
        else:
            return mocker.MagicMock()

    mock_process_pool = mocker.patch("xraybot._worker.ProcessPoolExecutor.map")
    mock_process_pool.side_effect = mock_side_effect
    mocker.patch("xraybot._context.XrayBotContext.xray")
    mock_jira = mocker.patch("xraybot._context.XrayBotContext.jira")
    mock_jira.username = "username"
    mock_jira.jql.side_effect = jql_side_effect
    mock_jira.get_issue.side_effect = get_issue_side_effect
    mock_jira.get_issue_status.side_effect = get_issue_status_side_effect
    mock_jira.get_all_custom_fields.return_value = _get_response("custom_fields")
    marked_test = TestEntity(
        key="demo-999",
        summary="Marked Foo",
        description="Marked Foo desc",
        req_key="REQ-109",
        unique_identifier="tests/my-directory/test_marked.py::Foo_999",
    )

    xray_bot.sync_tests([*local_tests, marked_test])
    # create 2 cases
    assert mock_jira.create_issue.call_args_list == [
        call(
            {
                "issuetype": {"name": "Test"},
                "project": {"key": "DEMO"},
                "description": "Foo desc",
                "summary": "Foo",
                "assignee": {"name": "username"},
                "customfield_100": "tests/my-directory/test_demo.py::Foo",
                "customfield_15095": {"value": "Generic"},
            }
        ),
        call(
            {
                "issuetype": {"name": "Test"},
                "project": {"key": "DEMO"},
                "description": "Bar desc",
                "summary": "Bar",
                "assignee": {"name": "username"},
                "customfield_100": "tests/my-directory/test_demo.py::Bar",
                "customfield_15095": {"value": "Generic"},
            }
        ),
    ]
    # Obsolete one deleted test and finalize 2 new tests, 1 marked test
    assert mock_jira.set_issue_status.call_args_list == [
        call("DEMO-999", "In-Draft"),
        call("DEMO-999", "Ready for Review"),
        call("DEMO-999", "In Review"),
        call("DEMO-999", "Finalized"),
        call("DEMO-9", "Obsolete"),
        call(ANY, "Ready for Review"),
        call(ANY, "In Review"),
        call(ANY, "Finalized"),
        call(ANY, "Ready for Review"),
        call(ANY, "In Review"),
        call(ANY, "Finalized"),
    ]
    # update case
    assert mock_jira.update_issue_field.call_args_list == [
        call(
            key="DEMO-999",
            fields={
                "description": "Marked Foo desc",
                "summary": "Marked Foo",
                "assignee": {"name": "username"},
                "reporter": {"name": "username"},
                "customfield_100": "tests/my-directory/test_marked.py::Foo_999",
                "customfield_15095": {"value": "Generic"},
                "labels": [],
            },
        ),
        call(
            key="DEMO-11",
            fields={"summary": "test_error", "description": "updated desc"},
        ),
    ]


def test_xray_sync_partially_fail(mocker):
    xray_bot = XrayBot(mock_url, mock_username, mock_pwd, mock_project_key)

    def mock_side_effect(worker_wrapper, func_list, *iterables):
        results = []
        for idx, func in enumerate(func_list):
            from xraybot._worker import _ExternalMarkedTestUpdateWorker

            if isinstance(func.__self__, _ExternalMarkedTestUpdateWorker):

                def _fail_mock(*_, **__):
                    raise AssertionError("fail")

                func = _fail_mock
            results.append(worker_wrapper(func, *list(zip(*iterables))[idx]))
        return results

    def get_issue_side_effect(key, *_, **__):
        if key == "DEMO-999":
            return _get_response("get_issue")
        else:
            return mocker.MagicMock()

    jql_side_effect_counter = 0

    def jql_side_effect(*_, **__):
        nonlocal jql_side_effect_counter
        jql_side_effect_counter = jql_side_effect_counter + 1
        if jql_side_effect_counter == 1:
            return _get_response("search")
        else:
            return _get_response("search_after_with_marked")

    get_issue_status_counter = 0

    def get_issue_status_side_effect(key):
        nonlocal get_issue_status_counter
        get_issue_status_counter = get_issue_status_counter + 1
        if key == "DEMO-999":
            if get_issue_status_counter == 1:
                return "In-Draft"
            else:
                return "Finalized"
        else:
            return mocker.MagicMock()

    mock_process_pool = mocker.patch("xraybot._worker.ProcessPoolExecutor.map")
    mock_process_pool.side_effect = mock_side_effect
    mocker.patch("xraybot._context.XrayBotContext.xray")
    mock_jira = mocker.patch("xraybot._context.XrayBotContext.jira")
    mock_jira.username = "username"
    mock_jira.jql.side_effect = jql_side_effect
    mock_jira.get_issue.side_effect = get_issue_side_effect
    mock_jira.get_issue_status.side_effect = get_issue_status_side_effect
    mock_jira.get_all_custom_fields.return_value = _get_response("custom_fields")
    marked_test = TestEntity(
        key="demo-999",
        summary="Marked Foo",
        description="Marked Foo desc",
        req_key="REQ-109",
        unique_identifier="tests/my-directory/test_marked.py::Foo_999",
    )
    with pytest.raises(AssertionError) as e:
        xray_bot.sync_tests([*local_tests, marked_test])
    err_msg = "❌fail -> 🐛TestEntity(unique_identifier='tests/my-directory/test_marked.py::Foo_999', summary='Marked Foo', description='Marked Foo desc', req_key='REQ-109', key='DEMO-999')"
    assert err_msg in e.value.args[0]
    # create 2 cases
    assert mock_jira.create_issue.call_args_list == [
        call(
            {
                "issuetype": {"name": "Test"},
                "project": {"key": "DEMO"},
                "description": "Foo desc",
                "summary": "Foo",
                "assignee": {"name": "username"},
                "customfield_100": "tests/my-directory/test_demo.py::Foo",
                "customfield_15095": {"value": "Generic"},
            }
        ),
        call(
            {
                "issuetype": {"name": "Test"},
                "project": {"key": "DEMO"},
                "description": "Bar desc",
                "summary": "Bar",
                "assignee": {"name": "username"},
                "customfield_100": "tests/my-directory/test_demo.py::Bar",
                "customfield_15095": {"value": "Generic"},
            }
        ),
    ]
    # Obsolete one deleted test and finalize 2 new tests, 1 marked test
    assert mock_jira.set_issue_status.call_args_list == [
        call("DEMO-9", "Obsolete"),
        call(ANY, "Ready for Review"),
        call(ANY, "In Review"),
        call(ANY, "Finalized"),
        call(ANY, "Ready for Review"),
        call(ANY, "In Review"),
        call(ANY, "Finalized"),
    ]
    # update case
    assert mock_jira.update_issue_field.call_args_list == [
        call(
            key="DEMO-11",
            fields={"summary": "test_error", "description": "updated desc"},
        ),
    ]
