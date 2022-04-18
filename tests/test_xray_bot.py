import os
import json
import pytest
from copy import deepcopy
from unittest.mock import call, ANY
from xraybot import XrayBot, TestEntity, TestResultEntity, XrayResultType, _xray_bot

tests_dir = os.path.abspath(os.path.dirname(__file__))
root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


@pytest.fixture(autouse=True)
def setup():
    yield


mock_url = "http://test.com"
mock_username = "username"
mock_pwd = "pwd"
mock_project_key = "DEMO"
mock_search_request = (
    f"{mock_url}/rest/api/2/search?startAt=0&maxResults=-1&"
    f"fields=summary%2Cdescription%2Cissuelinks&"
    f"jql=project+%3D+%22{mock_project_key}%22+and+type+%3D+%22Test%22+"
    f"and+reporter+%3D+%22{mock_username}%22+"
    f"and+status+%21%3D+%22Obsolete%22"
)
mock_xray_tests = [
    TestEntity(
        key="DEMO-11",
        req_key="REQ-120",
        summary="test_error",
        description="tests the multi error",
    ),
    TestEntity(
        key="DEMO-10",
        req_key="REQ-120",
        summary="test_invalid_email_format",
        description="Summary: Check the invalid email format\nExpectation: the warning is same as the design\n"
        "1. input a invalid email format\n2. click the login button",
    ),
    TestEntity(
        key="DEMO-9",
        req_key="REQ-120",
        summary="test_login_app_invalid_account",
        description="Summary: Use the invalid account to login the app\n"
        "Expectation: the warning is same as the design\n1. input a invalid account and password\n"
        "2. click the login button",
    ),
]
cpy_mock_xray_tests = deepcopy(mock_xray_tests)
to_be_appended1 = TestEntity(summary="Foo", description="Foo desc", req_key="REQ-100")
to_be_appended2 = TestEntity(summary="Bar", description="Bar desc", req_key="REQ-101")
to_be_updated = cpy_mock_xray_tests[0]
to_be_updated.description = "updated desc"
to_be_deleted = cpy_mock_xray_tests[2]
local_tests = [to_be_appended1, to_be_appended2, to_be_updated, cpy_mock_xray_tests[1]]


def _get_response(name) -> dict:
    with open(os.path.join(tests_dir, "response", f"{name}.json")) as f:
        return json.load(f)


def test_get_xray_tests(requests_mock):
    requests_mock.get(mock_search_request, json=_get_response("search"))
    xray_bot = XrayBot(mock_url, mock_username, mock_pwd, mock_project_key)
    xray_tests = xray_bot.get_xray_tests()
    assert xray_tests == mock_xray_tests


def test_get_xray_tests_with_cf_value_str(requests_mock):
    requests_mock.get(
        mock_search_request + "+and+%22Test+Type%22+%3D+%22Automated%22",
        json=_get_response("search"),
    )
    xray_bot = XrayBot(mock_url, mock_username, mock_pwd, mock_project_key)
    xray_bot.configure_custom_field("Test Type", "Automated")
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
        mock_search_request
        + "+and+%22Test+Type%22+%3D+%22Automated%22+and+%22Test+Case+Platform%22+in+%28%22Android%22%29",
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
    _to_be_deleted, _to_be_appended, _to_be_updated = xray_bot._get_tests_diff(
        xray_tests, local_tests
    )
    assert _to_be_deleted == [to_be_deleted]
    assert _to_be_appended == [to_be_appended1, to_be_appended2]
    assert _to_be_updated == [to_be_updated]


def test_xray_sync(mocker):
    xray_bot = XrayBot(mock_url, mock_username, mock_pwd, mock_project_key)

    def mock_side_effect(fun, params):
        for param in params:
            fun(param)

    mock_process_pool_map = mocker.patch.object(_xray_bot.ProcessPoolExecutor, "map")
    mock_process_pool_map.side_effect = mock_side_effect
    mock_jira = mocker.patch.object(xray_bot, "_jira")
    mock_jira.jql.return_value = _get_response("search")
    mocker.patch.object(xray_bot, "_xray")
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
            }
        ),
        call(
            {
                "issuetype": {"name": "Test"},
                "project": {"key": "DEMO"},
                "description": "Bar desc",
                "summary": "Bar",
                "assignee": {"name": "username"},
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

    def mock_side_effect(fun, params):
        for param in params:
            fun(param)

    mock_process_pool_map = mocker.patch.object(_xray_bot.ProcessPoolExecutor, "map")
    mock_process_pool_map.side_effect = mock_side_effect
    mock_jira = mocker.patch.object(xray_bot, "_jira")
    mock_jira.get_all_custom_fields.return_value = _get_response("custom_fields")
    mock_jira.jql.return_value = _get_response("search")
    mocker.patch.object(xray_bot, "_xray")
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

    def mock_side_effect(fun, params):
        for param in params:
            fun(param)

    mock_process_pool_map = mocker.patch.object(_xray_bot.ProcessPoolExecutor, "map")
    mock_process_pool_map.side_effect = mock_side_effect
    mock_jira = mocker.patch.object(xray_bot, "_jira")
    mock_jira.get_all_custom_fields.return_value = _get_response("custom_fields")
    mock_jira.jql.return_value = _get_response("search")
    mocker.patch.object(xray_bot, "_xray")
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

    def mock_side_effect(fun, params):
        for param in params:
            fun(param)

    mock_process_pool_map = mocker.patch.object(_xray_bot.ProcessPoolExecutor, "map")
    mock_process_pool_map.side_effect = mock_side_effect
    mock_jira = mocker.patch.object(xray_bot, "_jira")
    mock_jira.get_all_custom_fields.return_value = _get_response("custom_fields")
    mock_jira.jql.return_value = _get_response("search")
    mocker.patch.object(xray_bot, "_xray")
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

    def mock_side_effect(fun, *args):
        for arg in zip(*args):
            fun(*arg)

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

    mock_process_pool_map = mocker.patch.object(_xray_bot.ProcessPoolExecutor, "map")
    mock_process_pool_map.side_effect = mock_side_effect
    mock_jira = mocker.patch.object(xray_bot, "_jira")
    mock_jira.create_issue.side_effect = create_issue_side_effect
    mock_jira.jql.return_value = _get_response("search")
    mock_xray = mocker.patch.object(xray_bot, "_xray")
    mock_xray.get_test_runs.return_value = [
        {"testExecKey": test_exec_id, "id": test_run_id}
    ]
    test_results = [
        TestResultEntity(key="DEMO-10", result=XrayResultType.PASS),
        TestResultEntity(key="DEMO-9", result=XrayResultType.FAIL),
        TestResultEntity(key="DEMO-11", result=XrayResultType.TODO),
    ]
    xray_bot.upload_automation_results("test_plan", "test_exec", test_results)
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
    assert mock_xray.update_test_plan_test_executions.call_args_list == [
        call(100, add=[101])
    ]
    assert mock_xray.update_test_run_status.call_args_list == [
        call(test_run_id, "PASS"),
        call(test_run_id, "FAIL"),
        call(test_run_id, "TODO"),
    ]
