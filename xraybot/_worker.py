from abc import abstractmethod
from enum import Enum
from typing import List
from concurrent.futures.process import ProcessPoolExecutor
from ._data import TestEntity
from ._utils import logger
from ._context import XrayBotContext


class _XrayAPIWrapper:
    def __init__(self, context: XrayBotContext):
        self.context = context
        self._automation_folder_id_cache = None
        self._automation_obsolete_folder_id_cache = None

    @property
    def automation_folder_id(self):
        if self._automation_folder_id_cache is None:
            self._automation_folder_id_cache = self.create_repo_folder(
                self.context.config.automation_folder_name, -1
            )
        return self._automation_folder_id_cache

    @property
    def automation_obsolete_folder_id(self):
        if self._automation_obsolete_folder_id_cache is None:
            self._automation_obsolete_folder_id_cache = self.create_repo_folder(
                self.context.config.obsolete_automation_folder_name,
                self.automation_folder_id,
            )
        return self._automation_obsolete_folder_id_cache

    def delete_test(self, test_entity: TestEntity):
        logger.info(f"Start deleting test: {test_entity.key}")
        self.context.jira.delete_issue(test_entity.key)

    def remove_links(self, test_entity: TestEntity):
        issue = self.context.jira.get_issue(test_entity.key)
        for link in issue["fields"]["issuelinks"]:
            if link["type"]["name"] == "Tests":
                self.context.jira.remove_issue_link(link["id"])

    def finalize_test(self, test_entity: TestEntity):
        logger.info(f"Start finalizing test: {test_entity.key}")
        try:
            self.context.jira.set_issue_status(test_entity.key, "Ready for Review")
            self.context.jira.set_issue_status(test_entity.key, "In Review")
            self.context.jira.set_issue_status(test_entity.key, "Finalized")
        except Exception as e:
            logger.warning(f"Finalize test with error: {e}")

    def link_test(self, test_entity: TestEntity):
        if test_entity.req_key:
            # support multi req keys
            req_key_list = test_entity.req_key.split(",")
            for _req_key in req_key_list:
                logger.info(
                    f"Start linking test {test_entity.key} to requirement: {_req_key}"
                )
                link_param = {
                    "type": {"name": "Tests"},
                    "inwardIssue": {"key": test_entity.key},
                    "outwardIssue": {"key": _req_key},
                }
                try:
                    self.context.jira.create_issue_link(link_param)
                except Exception as e:
                    logger.warning(f"Link test with error: {e}")

    def add_test_into_folder(self, test_entity: TestEntity, folder_id: int):
        try:
            self.context.xray.put(
                f"rest/raven/1.0/api/testrepository/"
                f"{self.context.project_key}/folders/{folder_id}/tests",
                data={"add": [test_entity.key]},
            )
        except Exception as e:
            logger.warning(f"Move test to repo folder with error: {e}")

    def remove_test_from_folder(self, test_entity: TestEntity, folder_id: int):
        self.context.xray.put(
            f"rest/raven/1.0/api/testrepository/"
            f"{self.context.project_key}/folders/{folder_id}/tests",
            data={"remove": [test_entity.key]},
        )

    def create_repo_folder(self, folder_name: str, parent_id: int) -> int:
        all_folders = self.context.xray.get(
            f"rest/raven/1.0/api/testrepository/{self.context.project_key}/folders"
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
            folder = self.context.xray.post(
                f"rest/raven/1.0/api/testrepository/{self.context.project_key}/folders/{parent_id}",
                data={"name": folder_name},
            )
            folder_id = folder["id"]
        return folder_id

    def finalize_test_from_any_status(self, test_entity: TestEntity):
        logger.info(f"Start finalizing marked test: {test_entity.key}")
        status = self.context.jira.get_issue_status(test_entity.key)
        if status == "Finalized":
            return

        for status in ["Ready for Review", "In Review", "Finalized"]:
            try:
                self.context.jira.set_issue_status(test_entity.key, status)
            except Exception as e:
                logger.warning(f"Update test status with error: {e}")

        status = self.context.jira.get_issue_status(test_entity.key)
        assert (
            status == "Finalized"
        ), f"Marked test {test_entity.key} cannot be finalized."

    def renew_test_details(self, marked_test: TestEntity):
        assert marked_test.key is not None, "Marked test key cannot be None"
        result = self.context.jira.get_issue(
            marked_test.key, fields=("project", "issuetype", "status")
        )
        assert (
            result["fields"]["project"]["key"] == self.context.project_key
        ), f"Marked test {marked_test.key} is not belonging to current project."
        assert (
            result["fields"]["issuetype"]["name"] == "Test"
        ), f"Marked test {marked_test.key} is not a test at all."
        assert (
            result["fields"]["status"]["name"] != "Obsolete"
        ), f"Marked test {marked_test.key} is obsolete."

        fields = {
            "description": marked_test.description,
            "summary": marked_test.summary,
            "assignee": {"name": self.context.jira.username},
            "reporter": {"name": self.context.jira.username},
            self.context.config.cf_id_test_definition: marked_test.unique_identifier,
            **self.context.config.get_tests_cf_label_fields(),
        }

        self.context.jira.update_issue_field(
            key=marked_test.key,
            fields=fields,
        )

    def create_test_plan(self, test_plan_name: str) -> str:
        jql = (
            f'project = "{self.context.project_key}" and type="Test Plan" and '
            f'reporter= "{self.context.jira_username}"'
        )

        for _ in self.context.jira.jql(jql, limit=-1)["issues"]:
            if _["fields"]["summary"] == test_plan_name:
                key = _["key"]
                logger.info(f"Found existing test plan: {key}")
                return key

        fields = {
            "issuetype": {"name": "Test Plan"},
            "project": {"key": self.context.project_key},
            "summary": test_plan_name,
            "assignee": {"name": self.context.jira_username},
        }

        test_plan_ticket = self.context.jira.create_issue(fields)
        key = test_plan_ticket["key"]
        logger.info(f"Created new test plan: {key}")
        return key

    def create_test_execution(self, test_execution_name: str) -> str:
        jql = (
            f'project = "{self.context.project_key}" and type="Test Execution" '
            f'and reporter= "{self.context.jira_username}"'
        )

        for _ in self.context.jira.jql(jql, limit=-1)["issues"]:
            if _["fields"]["summary"] == test_execution_name:
                key = _["key"]
                logger.info(f"Found existing test execution: {key}")
                return key

        fields = {
            "issuetype": {"name": "Test Execution"},
            "project": {"key": self.context.project_key},
            "summary": test_execution_name,
            "assignee": {"name": self.context.jira_username},
        }

        test_plan_ticket = self.context.jira.create_issue(fields)
        key = test_plan_ticket["key"]
        logger.info(f"Created new test execution: {key}")
        return key

    def get_tests_from_test_plan(self, test_plan_key) -> List[str]:
        page = 1
        tests = []
        while True:
            results = self.context.xray.get_tests_with_test_plan(
                test_plan_key, limit=self.context.config.query_page_limit, page=page
            )
            results = [result["key"] for result in results]
            tests = tests + results
            if len(results) == 0:
                break
            else:
                page = page + 1
        return tests

    def get_tests_from_test_execution(self, test_execution_key) -> List[str]:
        page = 1
        tests = []
        while True:
            results = self.context.xray.get_tests_with_test_execution(
                test_execution_key,
                limit=self.context.config.query_page_limit,
                page=page,
            )
            results = [result["key"] for result in results]
            tests = tests + results
            if len(results) == 0:
                break
            else:
                page = page + 1
        return tests


class _XrayBotWorker:
    def __init__(self, api_wrapper: _XrayAPIWrapper):
        self.api_wrapper = api_wrapper
        self.context = self.api_wrapper.context

    @abstractmethod
    def run(self, *args):
        pass


class _NonMarkedTestObsoleteWorker(_XrayBotWorker):
    def run(self, test_entity: TestEntity):
        logger.info(f"Start obsoleting test: {test_entity.key}")
        self.context.jira.set_issue_status(test_entity.key, "Obsolete")
        self.api_wrapper.remove_links(test_entity)
        self.api_wrapper.remove_test_from_folder(
            test_entity, self.api_wrapper.automation_folder_id
        )
        self.api_wrapper.add_test_into_folder(
            test_entity, self.api_wrapper.automation_obsolete_folder_id
        )


class _NonMarkedTestCreateWorker(_XrayBotWorker):
    def run(self, test_entity: TestEntity):
        logger.info(f"Start creating test: {test_entity.summary}")

        fields = {
            "issuetype": {"name": "Test"},
            "project": {"key": self.context.project_key},
            "description": test_entity.description,
            "summary": test_entity.summary,
            "assignee": {"name": self.context.jira.username},
            self.context.config.cf_id_test_definition: test_entity.unique_identifier,
            **self.context.config.get_tests_cf_label_fields(),
        }

        test_entity.key = self.context.jira.create_issue(fields)["key"]
        logger.info(f"Created xray test: {test_entity.key}")
        self.api_wrapper.finalize_test(test_entity)
        self.api_wrapper.link_test(test_entity)
        self.api_wrapper.add_test_into_folder(
            test_entity, self.api_wrapper.automation_folder_id
        )


class _NonMarkedTestUpdateWorker(_XrayBotWorker):
    def run(self, test_entity: TestEntity):
        logger.info(f"Start updating test: {test_entity.key}")
        assert test_entity.key is not None, "Jira test key cannot be None"
        fields = {
            "summary": test_entity.summary,
            "description": test_entity.description,
        }
        self.context.jira.update_issue_field(
            key=test_entity.key,
            fields=fields,
        )
        self.api_wrapper.remove_links(test_entity)
        self.api_wrapper.link_test(test_entity)


class _ExternalMarkedTestUpdateWorker(_XrayBotWorker):
    def run(self, test_entity: TestEntity):
        logger.info(f"Start updating external marked test: {test_entity.key}")
        self.api_wrapper.renew_test_details(test_entity)
        self.api_wrapper.finalize_test(test_entity)
        self.api_wrapper.remove_links(test_entity)
        self.api_wrapper.link_test(test_entity)
        self.api_wrapper.add_test_into_folder(
            test_entity, self.api_wrapper.automation_folder_id
        )


class _InternalMarkedTestUpdateWorker(_XrayBotWorker):
    def run(self, test_entity: TestEntity):
        logger.info(f"Start updating internal marked test: {test_entity.key}")
        assert test_entity.key is not None, "Jira test key cannot be None"
        fields = {
            "summary": test_entity.summary,
            "description": test_entity.description,
            self.context.config.cf_id_test_definition: test_entity.unique_identifier,
        }
        self.context.jira.update_issue_field(
            key=test_entity.key,
            fields=fields,
        )
        self.api_wrapper.remove_links(test_entity)
        self.api_wrapper.link_test(test_entity)


class _AddTestsToPlanWorker(_XrayBotWorker):
    def run(self, test_plan_key: str, test_key: str):
        test_plans = self.context.xray.get_test_plans(test_key)
        if test_plan_key not in [_["key"] for _ in test_plans]:
            logger.info(f"Start adding test {test_key} to test plan {test_plan_key}")
            self.context.xray.update_test_plan(test_plan_key, add=[test_key])


class _AddTestsToExecutionWorker(_XrayBotWorker):
    def run(self, test_execution_key: str, test_key: str):
        test_executions = self.context.xray.get_test_executions(test_key)
        if test_execution_key not in [_["key"] for _ in test_executions]:
            logger.info(
                f"Start adding test {test_key} to test execution {test_execution_key}"
            )
            self.context.xray.update_test_execution(test_execution_key, add=[test_key])


class _UpdateTestResultsWorker(_XrayBotWorker):
    def run(self, test_key: str, result: str, test_execution_key: str):
        test_runs = self.context.xray.get_test_runs(test_key)
        for test_run in test_runs:
            if test_run["testExecKey"] == test_execution_key:
                logger.info(f"Start updating test run {test_key} result to {result}")
                self.context.xray.update_test_run_status(test_run["id"], result)


class _CleanTestExecutionWorker(_XrayBotWorker):
    def run(self, test_execution_key: str, test_key: str):
        status = self.context.jira.get_issue_status(test_key)
        if status != "Finalized":
            logger.info(
                f"Start deleting obsolete test {test_key} from test execution {test_execution_key}"
            )
            self.context.xray.delete_test_from_test_execution(
                test_execution_key, test_key
            )


class _CleanTestPlanWorker(_XrayBotWorker):
    def run(self, test_plan_key: str, test_key: str):
        status = self.context.jira.get_issue_status(test_key)
        if status != "Finalized":
            logger.info(
                f"Start deleting obsolete test {test_key} from test plan {test_plan_key}"
            )
            self.context.xray.delete_test_from_test_plan(test_plan_key, test_key)


class WorkerType(Enum):
    NonMarkedTestObsolete = _NonMarkedTestObsoleteWorker
    NonMarkedTestCreate = _NonMarkedTestCreateWorker
    NonMarkedTestUpdate = _NonMarkedTestUpdateWorker
    ExternalMarkedTestUpdate = _ExternalMarkedTestUpdateWorker
    InternalMarkedTestUpdate = _InternalMarkedTestUpdateWorker
    AddTestsToPlan = _AddTestsToPlanWorker
    AddTestsToExecution = _AddTestsToExecutionWorker
    UpdateTestResults = _UpdateTestResultsWorker
    CleanTestExecution = _CleanTestExecutionWorker
    CleanTestPlan = _CleanTestPlanWorker


class XrayBotWorkerMgr:
    def __init__(self, context: XrayBotContext):
        self.context = context
        self.api_wrapper = _XrayAPIWrapper(self.context)

    @staticmethod
    def _worker_wrapper(worker_func, *iterables):
        try:
            ret = worker_func(*iterables)
            return ret
        except Exception as e:
            logger.info(
                f"Worker [{worker_func.__qualname__.split('.')[0].lstrip('_')}] raised error: {e}"
            )
            raise e

    def start_worker(self, worker_type: WorkerType, *iterables):
        worker: _XrayBotWorker = worker_type.value(self.api_wrapper)
        with ProcessPoolExecutor(self.context.config.worker_num) as executor:
            executor.map(
                self._worker_wrapper,
                [worker.run for _ in range(len(iterables[0]))],
                *iterables,
            )
