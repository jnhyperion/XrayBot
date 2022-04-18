import logging
from enum import Enum
from typing import List, Tuple, Union, Dict
from atlassian import Jira, Xray
from dataclasses import dataclass
from concurrent.futures import ProcessPoolExecutor

logger = logging
logger_kwargs = {
    "level": logging.INFO,
    "format": "%(asctime)s %(levelname)s - %(message)s",
    "force": True,
}
logger.basicConfig(**logger_kwargs)


@dataclass
class TestEntity:
    key: str = None
    req_key: str = None
    summary: str = None
    description: str = None


class XrayResultType(Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    TODO = "TODO"


@dataclass
class TestResultEntity:
    key: str
    result: XrayResultType


class XrayBot:

    _MULTI_PROCESS_WORKER_NUM = 30
    _AUTOMATION_TESTS_FOLDER_NAME = "Automation Test"
    _AUTOMATION_OBSOLETE_TESTS_FOLDER_NAME = "Obsolete"

    def __init__(
        self, jira_url: str, jira_username: str, jira_pwd: str, project_key: str
    ):
        """
        :param jira_url: str
        :param jira_username: str
        :param jira_pwd: str
        :param project_key: str, jira project key, e.g: "TEST"
        """
        self._jira_url = jira_url
        self._jira_username = jira_username
        self._jira_pwd = jira_pwd
        self._project_key = project_key
        self._automation_folder_id = -1
        self._automation_obsolete_folder_id = -1
        self._jira = Jira(
            url=self._jira_url, username=self._jira_username, password=self._jira_pwd
        )
        self._xray = Xray(
            url=self._jira_url, username=self._jira_username, password=self._jira_pwd
        )
        self._custom_fields: Dict[str, Union[str, List[str]]] = {}
        self._cached_all_custom_fields = None

    def configure_custom_field(
        self, field_name: str, field_value: Union[str, List[str]]
    ):
        """
        :param field_name: str, custom field name
        :param field_value: custom field value of the test ticket
        e.g: field_value="value", field_value=["value1", "value2"]
        """
        self._custom_fields[field_name] = field_value

    def get_xray_tests(self) -> List[TestEntity]:
        logger.info(f"Start querying all xray tests for project: {self._project_key}")
        jql = (
            f'project = "{self._project_key}" and type = "Test" and reporter = "{self._jira_username}" '
            'and status != "Obsolete"'
        )
        for k, v in self._custom_fields.items():
            if isinstance(v, list) and v:
                converted = ",".join([f'"{_}"' for _ in v])
                jql = f'{jql} and "{k}" in ({converted})'
            else:
                jql = f'{jql} and "{k}" = "{v}"'
        logger.info(f"Querying jql: {jql}")
        tests = []
        for _ in self._jira.jql(
            jql, fields=["summary", "description", "issuelinks"], limit=-1
        )["issues"]:
            test = TestEntity(
                key=_["key"],
                summary=_["fields"]["summary"],
                description=_["fields"]["description"],
            )
            links = _["fields"]["issuelinks"]
            _req_keys = []
            for link in links:
                if link["type"]["name"] == "Tests":
                    _req_keys.append(link["outwardIssue"]["key"])
            if _req_keys:
                test.req_key = ",".join(_req_keys)
            tests.append(test)
        return tests

    def _get_custom_field_by_name(self, name: str):
        if not self._cached_all_custom_fields:
            self._cached_all_custom_fields = self._jira.get_all_custom_fields()
        for f in self._cached_all_custom_fields:
            if f["name"] == name:
                return f["id"]

    def _delete_test(self, test_entity: TestEntity):
        logger.info(f"Start deleting test: {test_entity.key}")
        self._jira.delete_issue(test_entity.key)

    def _obsolete_test(self, test_entity: TestEntity):
        logger.info(f"Start obsoleting test: {test_entity.key}")
        self._jira.set_issue_status(test_entity.key, "Obsolete")
        self._remove_links(test_entity)
        self._remove_case_from_folder(test_entity, self._automation_folder_id)
        self._add_case_into_folder(test_entity, self._automation_obsolete_folder_id)

    def _remove_links(self, test_entity: TestEntity):
        issue = self._jira.get_issue(test_entity.key)
        for link in issue["fields"]["issuelinks"]:
            if link["type"]["name"] == "Tests":
                self._jira.remove_issue_link(link["id"])

    def _update_jira_test(self, test_entity: TestEntity):
        logger.info(f"Start updating test: {test_entity.key}")
        self._jira.update_issue_field(
            key=test_entity.key,
            fields={
                "summary": test_entity.summary,
                "description": test_entity.description,
            },
        )
        self._remove_links(test_entity)
        self._link_test(test_entity)

    def _create_test(self, test_entity: TestEntity):
        logger.info(f"Start creating test: {test_entity.summary}")

        fields = {
            "issuetype": {"name": "Test"},
            "project": {"key": self._project_key},
            "description": test_entity.description,
            "summary": test_entity.summary,
            "assignee": {"name": self._jira_username},
        }

        for k, v in self._custom_fields.items():
            custom_field = self._get_custom_field_by_name(k)
            if isinstance(v, list) and v:
                fields[custom_field] = [{"value": _} for _ in v]
            else:
                fields[custom_field] = {"value": v}
        try:
            test_entity.key = self._jira.create_issue(fields)["key"]
        except Exception as e:
            logger.error(f"Create test with error: {e}")
            raise e
        logger.info(f"Created xray test: {test_entity.key}")
        self._finalize_new_test(test_entity)
        self._link_test(test_entity)
        self._add_case_into_folder(test_entity, self._automation_folder_id)

    def _finalize_new_test(self, test_entity: TestEntity):
        # only for new created xray test
        logger.info(f"Start finalizing test: {test_entity.key}")
        self._jira.set_issue_status(test_entity.key, "Ready for Review")
        self._jira.set_issue_status(test_entity.key, "In Review")
        self._jira.set_issue_status(test_entity.key, "Finalized")

    def _link_test(self, test_entity: TestEntity):
        if test_entity.req_key:
            # support multi req keys
            req_key_list = test_entity.req_key.split(",")
            for _req_key in req_key_list:
                logger.info(f"Start linking test to requirement: {test_entity.key}")
                link_param = {
                    "type": {"name": "Tests"},
                    "inwardIssue": {"key": test_entity.key},
                    "outwardIssue": {"key": _req_key},
                }
                self._jira.create_issue_link(link_param)

    def sync_tests(self, local_tests: List[TestEntity]):
        self._create_automation_repo_folder()
        xray_tests = self.get_xray_tests()
        to_be_deleted, to_be_appended, to_be_updated = self._get_tests_diff(
            xray_tests, local_tests
        )
        with ProcessPoolExecutor(self._MULTI_PROCESS_WORKER_NUM) as executor:
            executor.map(self._obsolete_test, to_be_deleted)

        with ProcessPoolExecutor(self._MULTI_PROCESS_WORKER_NUM) as executor:
            executor.map(self._create_test, to_be_appended)

        with ProcessPoolExecutor(self._MULTI_PROCESS_WORKER_NUM) as executor:
            executor.map(self._update_jira_test, to_be_updated)

    @staticmethod
    def _get_tests_diff(
        xray_tests: List[TestEntity], local_tests: List[TestEntity]
    ) -> Tuple[List[TestEntity], List[TestEntity], List[TestEntity]]:

        to_be_deleted = list()
        to_be_appended = list()
        to_be_updated = list()

        for test in xray_tests:
            if test.summary not in [_.summary for _ in local_tests]:
                # xray test not valid in xml anymore
                to_be_deleted.append(test)

        for test in local_tests:
            if test.summary not in [_.summary for _ in xray_tests]:
                # local test not exist in xray
                to_be_appended.append(test)

        for test in xray_tests:
            if test.summary in [_.summary for _ in local_tests]:
                # xray test already exists
                previous_description = (
                    test.description if test.description is not None else ""
                )
                previous_req_key = test.req_key if test.req_key is not None else ""
                new_description = [
                    _.description for _ in local_tests if test.summary == _.summary
                ][0]
                new_req_key = [
                    _.req_key for _ in local_tests if test.summary == _.summary
                ][0]
                if previous_description != new_description or set(
                    previous_req_key.split(",")
                ) != set(new_req_key.split(",")):
                    # test desc / requirement id is different
                    test.description = new_description
                    test.req_key = new_req_key
                    to_be_updated.append(test)

        return to_be_deleted, to_be_appended, to_be_updated

    def _create_test_plan(self, test_plan_name: str) -> str:
        jql = f'project = "{self._project_key}" and type="Test Plan" and reporter= "{self._jira_username}"'

        for _ in self._jira.jql(jql, limit=-1)["issues"]:
            if _["fields"]["summary"] == test_plan_name:
                key = _["key"]
                logger.info(f"Found existing test plan: {key}")
                return key

        fields = {
            "issuetype": {"name": "Test Plan"},
            "project": {"key": self._project_key},
            "summary": test_plan_name,
            "assignee": {"name": self._jira_username},
        }

        test_plan_ticket = self._jira.create_issue(fields)
        key = test_plan_ticket["key"]
        logger.info(f"Created new test plan: {key}")
        return key

    def _add_tests_to_test_plan(self, test_plan_key: str, test_key: str):
        test_plans = self._xray.get_test_plans(test_key)
        if test_plan_key not in [_["key"] for _ in test_plans]:
            logger.info(f"Start adding test {test_key} to test plan {test_plan_key}")
            self._xray.update_test_plan(test_plan_key, add=[test_key])

    def _add_tests_to_test_execution(self, test_execution_key: str, test_key: str):
        test_executions = self._xray.get_test_executions(test_key)
        if test_execution_key not in [_["key"] for _ in test_executions]:
            logger.info(
                f"Start adding test {test_key} to test execution {test_execution_key}"
            )
            self._xray.update_test_execution(test_execution_key, add=[test_key])

    def _add_test_execution_to_test_plan(
        self, test_execution_key: str, test_plan_key: str
    ):
        logger.info(
            f"Start adding test execution {test_execution_key} to test plan {test_plan_key}"
        )
        self._xray.update_test_plan_test_executions(
            test_plan_key, add=[test_execution_key]
        )

    def _create_test_execution(self, test_execution_name: str) -> str:
        jql = f'project = "{self._project_key}" and type="Test Execution" and reporter= "{self._jira_username}"'

        for _ in self._jira.jql(jql, limit=-1)["issues"]:
            if _["fields"]["summary"] == test_execution_name:
                key = _["key"]
                logger.info(f"Found existing test execution: {key}")
                return key

        fields = {
            "issuetype": {"name": "Test Execution"},
            "project": {"key": self._project_key},
            "summary": test_execution_name,
            "assignee": {"name": self._jira_username},
        }

        test_plan_ticket = self._jira.create_issue(fields)
        key = test_plan_ticket["key"]
        logger.info(f"Created new test execution: {key}")
        return key

    def _update_test_result(self, test_key: str, result: str, test_execution_key: str):
        test_runs = self._xray.get_test_runs(test_key)
        for test_run in test_runs:
            if test_run["testExecKey"] == test_execution_key:
                logger.info(f"Start updating test run {test_key} result to {result}")
                self._xray.update_test_run_status(test_run["id"], result)

    def _add_case_into_folder(self, test_entity: TestEntity, folder_id: int):
        self._xray.put(
            f"rest/raven/1.0/api/testrepository/"
            f"{self._project_key}/folders/{folder_id}/tests",
            data={"add": [test_entity.key]},
        )

    def _remove_case_from_folder(self, test_entity: TestEntity, folder_id: int):
        self._xray.put(
            f"rest/raven/1.0/api/testrepository/"
            f"{self._project_key}/folders/{folder_id}/tests",
            data={"remove": [test_entity.key]},
        )

    def _create_repo_folder(self, folder_name: str, parent_id: int) -> int:
        all_folders = self._xray.get(
            f"rest/raven/1.0/api/testrepository/{self._project_key}/folders"
        )

        def _iter_folders(folders):
            for _ in folders["folders"]:
                if _["id"] == parent_id:
                    return _["folders"]
                else:
                    _iter_folders(_)
            return []

        if parent_id == -1:
            sub_folders = all_folders["folders"]
        else:
            sub_folders = _iter_folders(all_folders)

        folder_id = -1
        for folder in sub_folders:
            if folder_name == folder["name"]:
                logger.info(f"Using existing test repo folder: {folder_name}")
                folder_id = folder["id"]
                break
        if folder_id == -1:
            logger.info(f"Create test repo folder: {folder_name}")
            folder = self._xray.post(
                f"rest/raven/1.0/api/testrepository/{self._project_key}/folders/{parent_id}",
                data={"name": folder_name},
            )
            folder_id = folder["id"]
        return folder_id

    def _create_automation_repo_folder(self):
        self._automation_folder_id = self._create_repo_folder(
            self._AUTOMATION_TESTS_FOLDER_NAME, -1
        )
        self._automation_obsolete_folder_id = self._create_repo_folder(
            self._AUTOMATION_OBSOLETE_TESTS_FOLDER_NAME, self._automation_folder_id
        )

    def upload_automation_results(
        self,
        test_plan_name: str,
        test_execution_name: str,
        test_results: List[TestResultEntity],
    ):
        test_plan_key = self._create_test_plan(test_plan_name)
        test_execution_key = self._create_test_execution(test_execution_name)
        tests = self.get_xray_tests()
        with ProcessPoolExecutor(self._MULTI_PROCESS_WORKER_NUM) as executor:
            # add tests to test plan
            executor.map(
                self._add_tests_to_test_plan,
                [test_plan_key for _ in range(len(tests))],
                [_.key for _ in tests],
            )

        with ProcessPoolExecutor(self._MULTI_PROCESS_WORKER_NUM) as executor:
            # add tests to test execution
            executor.map(
                self._add_tests_to_test_execution,
                [test_execution_key for _ in range(len(tests))],
                [_.key for _ in tests],
            )

        self._add_test_execution_to_test_plan(test_execution_key, test_plan_key)

        with ProcessPoolExecutor(self._MULTI_PROCESS_WORKER_NUM) as executor:
            # update test execution result
            executor.map(
                self._update_test_result,
                [result.key for result in test_results],
                [result.result.value for result in test_results],
                [test_execution_key for _ in range(len(test_results))],
            )
