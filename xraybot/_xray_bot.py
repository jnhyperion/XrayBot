import copy
from typing import List, Tuple, Union
from ._context import XrayBotContext
from ._data import TestEntity, TestResultEntity
from ._utils import logger
from ._worker import WorkerType, XrayBotWorkerMgr


_CF_TEST_DEFINITION = "Generic Test Definition"
_CF_TEST_TYPE = "Test Type"
_CF_TEST_TYPE_VAL_GENERIC = "Generic"
_CF_TEST_TYPE_VAL_MANUAL = "Manual"
_CF_TEST_TYPE_VAL_CUCUMBER = "Cucumber"


class XrayBot:
    _JIRA_API_TIMEOUT = 75
    _QUERY_PAGE_LIMIT = 100
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
        self.context = XrayBotContext(
            jira_url,
            jira_username,
            jira_pwd,
            project_key,
            timeout=self._JIRA_API_TIMEOUT,
        )
        self.config = self.context.config
        self.config.configure_query_page_limit(self._QUERY_PAGE_LIMIT)
        self.config.configure_worker_num(self._MULTI_PROCESS_WORKER_NUM)
        self.config.configure_automation_folder_name(self._AUTOMATION_TESTS_FOLDER_NAME)
        self.config.configure_obsolete_automation_folder_name(
            self._AUTOMATION_OBSOLETE_TESTS_FOLDER_NAME
        )
        self.worker_mgr = XrayBotWorkerMgr(self.context)

    def configure_custom_field(
        self, field_name: str, field_value: Union[str, List[str]]
    ):
        """
        :param field_name: str, custom field name
        :param field_value: custom field value of the test ticket
        e.g: field_value="value", field_value=["value1", "value2"]
        """
        self.config.configure_custom_field(field_name, field_value)

    def configure_labels(self, labels: List[str]):
        self.config.configure_labels(labels)

    def get_xray_tests(self, filter_by_cf: bool = True) -> List[TestEntity]:
        logger.info(
            f"Start querying all xray tests for project: {self.context.project_key}"
        )
        folder_id = self.worker_mgr.api_wrapper.automation_folder_id
        assert folder_id is not None
        # jql requires automation folder, need to make sure the folder exists
        jql = (
            f'project = "{self.context.project_key}" and type = "Test" and reporter = '
            f'"{self.context.jira_username}" '
            f'and status != "Obsolete" and issue in '
            f'testRepositoryFolderTests("{self.context.project_key}", '
            f'"{self.config.automation_folder_name}")'
        )
        if filter_by_cf:
            for k, v in self.config.custom_fields.items():
                if isinstance(v, list) and v:
                    converted = ",".join([f'"{_}"' for _ in v])
                    jql = f'{jql} and "{k}" in ({converted})'
                else:
                    jql = f'{jql} and "{k}" = "{v}"'

        if self.config.labels:
            _labels_filter = ",".join([f'"{_}"' for _ in self.config.labels])
            jql = f"{jql} and labels in ({_labels_filter})"

        logger.info(f"Querying jql: {jql}")
        tests = []
        for _ in self.context.jira.jql(
            jql,
            fields=[
                "summary",
                "description",
                "issuelinks",
                self.config.cf_id_test_definition,
            ],
            limit=-1,
        )["issues"]:
            desc = _["fields"]["description"]
            desc = desc if desc is not None else ""
            test = TestEntity(
                unique_identifier=_["fields"][self.config.cf_id_test_definition],
                summary=_["fields"]["summary"],
                description=desc,
                req_key="",
                key=_["key"],
            )
            links = _["fields"]["issuelinks"]
            _req_keys = []
            for link in links:
                if link["type"]["name"] == "Tests":
                    _req_keys.append(link["outwardIssue"]["key"])
            if _req_keys:
                test.req_key = ",".join(_req_keys)
            tests.append(test)
        self._check_duplicated_uniqueness(
            tests,
            "Duplicated key/unique_identifier found in xray_tests, you have to fix them manually.",
        )
        return tests

    @staticmethod
    def _check_duplicated_uniqueness(tests: List[TestEntity], error_msg):
        unique_identifiers = [t.unique_identifier for t in tests]
        duplicated_tests = [
            f"({idx+1}) {t}"
            for idx, t in enumerate(tests)
            if unique_identifiers.count(t.unique_identifier) > 1
        ]
        error_msg = error_msg + "\n" + "\n".join(duplicated_tests)
        assert len(duplicated_tests) == 0, error_msg
        keys = [t.key for t in tests if t.key is not None]
        duplicated_tests = [
            f"({idx + 1}) {t}"
            for idx, t in enumerate(tests)
            if t.key is not None and keys.count(t.key) > 1
        ]
        error_msg = error_msg + "\n" + "\n".join(duplicated_tests)
        assert len(duplicated_tests) == 0, error_msg

    @staticmethod
    def _categorize_local_tests(
        xray_tests: List[TestEntity], local_tests: List[TestEntity]
    ):
        xray_tests_keys = [_.key for _ in xray_tests]
        external_marked_local_tests = list()
        internal_marked_local_tests = list()
        non_marked_local_tests = list()
        for local_test in local_tests:
            if local_test.key is not None:
                if local_test.key in xray_tests_keys:
                    internal_marked_local_tests.append(local_test)
                else:
                    external_marked_local_tests.append(local_test)
            else:
                non_marked_local_tests.append(local_test)
        return (
            non_marked_local_tests,
            internal_marked_local_tests,
            external_marked_local_tests,
        )

    def create_tests_draft(self, local_tests: List[TestEntity]) -> List[TestEntity]:
        """
        Input: local tests including no existing jira key
        Output: local tests with draft tests created and key has been appended to test entity
        """
        local_tests_with_keys = copy.deepcopy(local_tests)
        # make sure all local test keys will be considered as upper case
        for local_test in local_tests_with_keys:
            if local_test.key is not None:
                local_test.key = local_test.key.upper()

        self._check_duplicated_uniqueness(
            local_tests_with_keys,
            "Duplicated key/unique_identifier found in local tests",
        )
        xray_tests = self.get_xray_tests()

        def get_test_key_by_unique_identifier(_local_test: TestEntity):
            for xray_test in xray_tests:
                if xray_test.unique_identifier == local_test.unique_identifier:
                    return xray_test.key
            return None

        to_be_created = []
        to_be_remained = []
        for local_test in local_tests_with_keys:
            if local_test.key is None:
                # tests have no key but already synced by unique identifier
                existing_key = get_test_key_by_unique_identifier(local_test)
                if existing_key is not None:
                    local_test.key = existing_key
                else:
                    to_be_created.append(local_test)
                    continue
            to_be_remained.append(local_test)

        worker_results = self.worker_mgr.start_worker(
            WorkerType.DraftTestCreate, to_be_created
        )
        errors = [result.data for result in worker_results if not result.success]
        err_msg = "\n".join(errors)
        assert len(errors) == 0, f"Create draft test failed:\n {err_msg}"
        results = to_be_remained + [_.data for _ in worker_results]
        return results

    def sync_tests(self, local_tests: List[TestEntity]):
        worker_results = []
        # make sure all local test keys will be considered as upper case
        for local_test in local_tests:
            if local_test.key is not None:
                local_test.key = local_test.key.upper()

        self._check_duplicated_uniqueness(
            local_tests, "Duplicated key/unique_identifier found in local_tests"
        )
        self.worker_mgr.api_wrapper.init_automation_repo_folder()
        xray_tests = self.get_xray_tests()
        (
            non_marked_local_tests,
            internal_marked_local_tests,
            external_marked_local_tests,
        ) = self._categorize_local_tests(xray_tests, local_tests)
        if external_marked_local_tests:
            # external marked test -> strategy: update and move to automation folder
            worker_results.extend(
                self.worker_mgr.start_worker(
                    WorkerType.ExternalMarkedTestUpdate, external_marked_local_tests
                )
            )
        if internal_marked_local_tests:
            # internal marked test -> strategy: update all fields including unique identifier
            filtered_xray_tests = [
                xray_test
                for xray_test in xray_tests
                if xray_test.key in [_.key for _ in internal_marked_local_tests]
            ]
            to_be_updated = self._get_internal_marked_tests_diff(
                filtered_xray_tests, internal_marked_local_tests
            )
            worker_results.extend(
                self.worker_mgr.start_worker(
                    WorkerType.InternalMarkedTestUpdate, to_be_updated
                )
            )

        # non marked test -> strategy: unique identifier sync
        # exclude internal marked local tests in xray tests
        # do not need to proceed unique identifier sync
        filtered_xray_tests = [
            xray_test
            for xray_test in xray_tests
            if xray_test.key not in [_.key for _ in internal_marked_local_tests]
        ]
        (
            to_be_deleted,
            to_be_appended,
            to_be_updated,
        ) = self._get_non_marked_tests_diff(filtered_xray_tests, non_marked_local_tests)
        worker_results.extend(
            self.worker_mgr.start_worker(
                WorkerType.NonMarkedTestObsolete, to_be_deleted
            )
        )
        worker_results.extend(
            self.worker_mgr.start_worker(WorkerType.NonMarkedTestCreate, to_be_appended)
        )
        worker_results.extend(
            self.worker_mgr.start_worker(WorkerType.NonMarkedTestUpdate, to_be_updated)
        )
        errors = [_.data for _ in worker_results if not _.success]
        if len(errors) > 0:
            err_msg = ""
            for idx, err in enumerate(errors):
                err_msg = f"{err_msg}\n({idx + 1}) {err}"
            raise AssertionError(f"Sync failed with the following errors:\n{err_msg}.")

    @staticmethod
    def _get_internal_marked_tests_diff(
        filtered_xray_tests: List[TestEntity],
        internal_marked_local_tests: List[TestEntity],
    ):
        to_be_updated = list()
        assert len(filtered_xray_tests) == len(
            internal_marked_local_tests
        ), "Internal marked test num is incorrect."

        for filtered_xray_test in filtered_xray_tests:
            previous_summary = filtered_xray_test.summary
            previous_description = filtered_xray_test.description
            previous_req_key = filtered_xray_test.req_key
            previous_unique_identifier = filtered_xray_test.unique_identifier
            matched_internal_marked_local_test: TestEntity = [
                _
                for _ in internal_marked_local_tests
                if filtered_xray_test.key == _.key
            ][0]
            new_summary = matched_internal_marked_local_test.summary
            new_description = matched_internal_marked_local_test.description
            new_req_key = matched_internal_marked_local_test.req_key
            new_unique_identifier = matched_internal_marked_local_test.unique_identifier
            if (
                previous_summary != new_summary
                or previous_description != new_description
                or previous_unique_identifier != new_unique_identifier
                or set(previous_req_key.split(",")) != set(new_req_key.split(","))
            ):
                # test desc / requirement id is different
                filtered_xray_test.summary = new_summary
                filtered_xray_test.description = new_description
                filtered_xray_test.req_key = new_req_key
                filtered_xray_test.unique_identifier = new_unique_identifier
                to_be_updated.append(filtered_xray_test)
        return to_be_updated

    @staticmethod
    def _get_non_marked_tests_diff(
        xray_tests: List[TestEntity], local_tests: List[TestEntity]
    ) -> Tuple[List[TestEntity], List[TestEntity], List[TestEntity]]:

        to_be_deleted = list()
        to_be_appended = list()
        to_be_updated = list()

        for test in xray_tests:
            if test.unique_identifier not in [_.unique_identifier for _ in local_tests]:
                # xray test not valid in xml anymore
                to_be_deleted.append(test)

        for test in local_tests:
            if test.unique_identifier not in [_.unique_identifier for _ in xray_tests]:
                # local test not exist in xray
                to_be_appended.append(test)

        for test in xray_tests:
            if test.unique_identifier in [_.unique_identifier for _ in local_tests]:
                # xray test already exists
                previous_summary = test.summary
                previous_description = test.description
                previous_req_key = test.req_key
                matched_local_test: TestEntity = [
                    _
                    for _ in local_tests
                    if test.unique_identifier == _.unique_identifier
                ][0]
                new_summary = matched_local_test.summary
                new_description = matched_local_test.description
                new_req_key = matched_local_test.req_key
                if (
                    previous_summary != new_summary
                    or previous_description != new_description
                    or set(previous_req_key.split(",")) != set(new_req_key.split(","))
                ):
                    # test desc / requirement id is different
                    test.summary = new_summary
                    test.description = new_description
                    test.req_key = new_req_key
                    to_be_updated.append(test)

        return to_be_deleted, to_be_appended, to_be_updated

    def _clean_test_plan_and_execution(
        self, test_execution_key: str, test_plan_key: str
    ):
        logger.info(
            f"Start cleaning test execution {test_execution_key} and test plan {test_plan_key}"
        )
        test_execution_tests = (
            self.worker_mgr.api_wrapper.get_tests_from_test_execution(
                test_execution_key
            )
        )
        test_plan_tests = self.worker_mgr.api_wrapper.get_tests_from_test_plan(
            test_plan_key
        )

        # delete obsolete tests from test execution
        self.worker_mgr.start_worker(
            WorkerType.CleanTestExecution,
            [test_execution_key for _ in range(len(test_execution_tests))],
            test_execution_tests,
        )
        # delete obsolete tests from test plan
        self.worker_mgr.start_worker(
            WorkerType.CleanTestPlan,
            [test_plan_key for _ in range(len(test_plan_tests))],
            test_plan_tests,
        )

    def upload_test_results(
        self,
        test_plan_name: str,
        test_execution_name: str,
        test_results: List[TestResultEntity],
        clean_test_plan_and_execution: bool = False,
    ):
        test_plan_key = self.worker_mgr.api_wrapper.create_test_plan(test_plan_name)
        test_execution_key = self.worker_mgr.api_wrapper.create_test_execution(
            test_execution_name
        )
        tests = self.get_xray_tests()
        # add tests to test plan based on all xray tests
        self.worker_mgr.start_worker(
            WorkerType.AddTestsToPlan,
            [test_plan_key for _ in range(len(tests))],
            [_.key for _ in tests],
        )

        # add tests to test execution based on local tests execution
        self.worker_mgr.start_worker(
            WorkerType.AddTestsToExecution,
            [test_execution_key for _ in range(len(test_results))],
            [_.key for _ in test_results],
        )

        logger.info(
            f"Start adding test execution {test_execution_key} to test plan {test_plan_key}"
        )
        # self.context.xray.update_test_plan_test_executions(
        #     test_plan_key, add=[test_execution_key]
        # )
        # workaround for xray testplan API performance issue
        self.context.jira.update_issue_field(
            test_execution_key, fields={self.config.cf_id_test_plan: [test_plan_key]}
        )

        if clean_test_plan_and_execution:
            self._clean_test_plan_and_execution(test_execution_key, test_plan_key)
        # update test execution result
        self.worker_mgr.start_worker(
            WorkerType.UpdateTestResults,
            [result.key for result in test_results],
            [result.result.value for result in test_results],
            [test_execution_key for _ in range(len(test_results))],
        )

    def upload_test_results_by_execution_key(
        self,
        test_results: List[TestResultEntity],
        test_execution_key: str,
    ):
        # add tests to test execution based on local tests execution
        self.worker_mgr.start_worker(
            WorkerType.AddTestsToExecution,
            [test_execution_key for _ in range(len(test_results))],
            [_.key for _ in test_results],
        )

        # update test execution result
        self.worker_mgr.start_worker(
            WorkerType.UpdateTestResults,
            [result.key for result in test_results],
            [result.result.value for result in test_results],
            [test_execution_key for _ in range(len(test_results))],
        )

    def sync_check(self, local_tests: List[TestEntity]):
        """
        1. make sure all local tests have been marked with keys
        2. make sure all the uniqueness of local tests keys and unique identifiers
        2. make sure all test keys are valid and not obsolete
        3. make sure requirement keys are valid
        """
        test_keys = [_.key for _ in local_tests]
        req_keys = [_.req_key for _ in local_tests]
        assert (
            None not in test_keys
        ), "Some of the tests are not are not marked with test key, run sync prepare firstly."

        self._check_duplicated_uniqueness(
            local_tests, "Duplicated key/unique_identifier found in local tests"
        )

        def chunks(xs, n=20):
            n = max(1, n)
            return list(xs[i : i + n] for i in range(0, len(xs), n))

        results = self.worker_mgr.start_worker(
            WorkerType.JiraStatusBulkCheck, chunks(test_keys)
        )
        errors = []
        test_status_results = []
        for result in results:
            if not result.success:
                errors.append(f"Get test status failed: {result}")
            else:
                test_status_results.extend(result.data)
        for test_key, status in test_status_results:
            if status == "Obsolete":
                errors.append(f"Test status is obsolete: {test_key}")

        merged_req_keys = []
        for req_key in req_keys:
            if req_key:
                merged_req_keys.extend(req_key.split(","))
        merged_req_keys = list(set(merged_req_keys))
        results = self.worker_mgr.start_worker(
            WorkerType.JiraStatusBulkCheck, chunks(merged_req_keys)
        )
        for result in results:
            if not result.success:
                errors.append(f"Get requirement status failed: {result}")
        if errors:
            err_msg = ""
            for idx, err in enumerate(errors):
                err_msg = f"{err_msg}\n({idx + 1}) {err}"
            raise AssertionError(f"Found following errors:{err_msg}")
