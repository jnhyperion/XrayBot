import copy
from typing import List, Union, Optional, Tuple
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
            f'"{self.config.automation_folder_name}", "true")'
        )
        if filter_by_cf:
            for k, v in self.config.custom_fields.items():
                if isinstance(v, list) and v:
                    converted = ",".join([f'"{_}"' for _ in v])
                    jql = f'{jql} and "{k}" in ({converted})'
                else:
                    jql = f'{jql} and "{k}" = "{v}"'

        logger.info(f"Querying jql: {jql}")
        tests = []
        issues = self.context.jira.jql(
            jql,
            fields=[
                "summary",
                "description",
                "issuelinks",
                "labels",
                self.config.cf_id_test_definition,
                self.config.cf_id_test_repo_path,
            ],
            limit=-1,
        )["issues"]
        for issue in issues:
            desc = issue["fields"]["description"]
            desc = desc if desc is not None else ""
            links = issue["fields"]["issuelinks"]
            labels = issue["fields"]["labels"]
            req_keys = [
                _["outwardIssue"]["key"]
                for _ in links
                if _["type"]["name"] == "Tests" and _.get("outwardIssue")
            ]
            defect_keys = [
                _["outwardIssue"]["key"]
                for _ in links
                if _["type"]["name"] == "Defect" and _.get("outwardIssue")
            ]
            test = TestEntity(
                key=issue["key"],
                unique_identifier=issue["fields"][self.config.cf_id_test_definition],
                summary=issue["fields"]["summary"],
                description=desc,
                labels=labels,
                repo_path=issue["fields"][self.config.cf_id_test_repo_path].split("/")[
                    2:
                ],
                req_keys=req_keys,
                defect_keys=defect_keys,
            )
            tests.append(test)
        self._check_tests_uniqueness(
            tests,
            "Duplicated key/unique_identifier found in xray tests, you have to fix them manually.",
        )
        return tests

    @staticmethod
    def _check_tests_uniqueness(tests: List[TestEntity], error_msg):
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
        local_tests_keys = [_.key for _ in local_tests]
        to_be_obsolete_xray_tests = list()
        external_marked_local_tests = list()
        internal_marked_local_tests = list()
        for local_test in local_tests:
            if local_test.key in xray_tests_keys:
                internal_marked_local_tests.append(local_test)
            else:
                external_marked_local_tests.append(local_test)
        for xray_test in xray_tests:
            if xray_test.key not in local_tests_keys:
                to_be_obsolete_xray_tests.append(xray_test)
        return (
            to_be_obsolete_xray_tests,
            internal_marked_local_tests,
            external_marked_local_tests,
        )

    def create_tests_draft(self, local_tests: List[TestEntity]) -> List[TestEntity]:
        """
        Input: local tests including no existing jira key
        Output: local tests with draft tests created and key has been appended to test entity
        """
        local_tests_cpy = copy.deepcopy(local_tests)
        to_be_created = []
        to_be_remained = []
        for local_test in local_tests_cpy:
            if local_test.key is not None:
                # make sure all local test keys will be considered as upper case
                local_test.key = local_test.key.upper()
                to_be_remained.append(local_test)
            else:
                to_be_created.append(local_test)

        self._check_tests_uniqueness(
            local_tests_cpy,
            "Duplicated key/unique_identifier found in local tests",
        )
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
            else:
                raise AssertionError(f"Local test {local_test} requires key in sync")

        self._check_tests_uniqueness(
            local_tests, "Duplicated key/unique_identifier found in local tests"
        )
        self.worker_mgr.api_wrapper.prepare_repo_folder_hierarchy(local_tests)
        xray_tests = self.get_xray_tests()
        (
            to_be_obsolete_xray_tests,
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

        # test only exists in xray tests while not in local tests
        if to_be_obsolete_xray_tests:
            worker_results.extend(
                self.worker_mgr.start_worker(
                    WorkerType.ObsoleteTest, to_be_obsolete_xray_tests
                )
            )
        errors = [_.data for _ in worker_results if not _.success]
        if len(errors) > 0:
            err_msg = ""
            for idx, err in enumerate(errors):
                err_msg = f"{err_msg}\n({idx + 1}) {err}"
            raise AssertionError(f"Sync failed with the following errors:\n{err_msg}.")
        logger.info("Start cleaning empty repo folders")
        self.worker_mgr.start_worker(
            WorkerType.CleanRepoFolder,
            self.worker_mgr.api_wrapper.get_all_sub_folders_id(),
        )

    @staticmethod
    def _get_internal_marked_tests_diff(
        filtered_xray_tests: List[TestEntity],
        internal_marked_local_tests: List[TestEntity],
    ):
        to_be_updated = list()
        assert len(filtered_xray_tests) == len(
            internal_marked_local_tests
        ), "Internal marked test num is incorrect."

        def get_matched_xray_test(key):
            matched_tests = [t for t in filtered_xray_tests if t.key == key]
            assert len(matched_tests) == 1, "Exact one match test is expected"
            return matched_tests[0]

        for local_test in internal_marked_local_tests:
            if local_test != get_matched_xray_test(local_test.key):
                to_be_updated.append(local_test)

        return to_be_updated

    def _clean_test_execution(self, test_execution_key: str):
        logger.info(f"Start cleaning test execution {test_execution_key}")
        test_execution_tests = (
            self.worker_mgr.api_wrapper.get_tests_from_test_execution(
                test_execution_key
            )
        )

        # delete obsolete tests from test execution
        self.worker_mgr.start_worker(
            WorkerType.CleanTestExecution,
            [test_execution_key for _ in range(len(test_execution_tests))],
            test_execution_tests,
        )

    def _clean_test_plan(self, test_plan_key: str):
        logger.info(f"Start cleaning test plan {test_plan_key}")
        test_plan_tests = self.worker_mgr.api_wrapper.get_tests_from_test_plan(
            test_plan_key
        )
        # delete obsolete tests from test plan
        self.worker_mgr.start_worker(
            WorkerType.CleanTestPlan,
            [test_plan_key for _ in range(len(test_plan_tests))],
            test_plan_tests,
        )

    def _update_test_plan_execution_status(self, key):
        for status in ["In Progress", "Executed"]:
            try:
                self.context.jira.set_issue_status(key, status)
            except Exception as e:
                # ignore errors from any status
                logger.debug(f"Update test plan/execution status with error: {e}")

    def _assert_test_results_exist_in_xray_tests(
        self, test_results: List[TestResultEntity]
    ):
        xray_tests = self.get_xray_tests()
        xray_tests_keys = [t.key for t in xray_tests]
        for result in test_results:
            assert (
                result.key in xray_tests_keys
            ), f"Unrecognized test {result.key} from test results"

    def upload_test_results_by_key(
        self,
        test_execution_key: str,
        test_results: List[TestResultEntity],
        test_plan_key: Optional[str] = None,
        clean_obsolete: bool = False,
        full_test_set: bool = False,
        ignore_missing: bool = False,
    ):
        if not ignore_missing:
            self._assert_test_results_exist_in_xray_tests(test_results)

        if full_test_set:
            tests = self.get_xray_tests()
            test_keys = [t.key for t in tests]
        else:
            test_keys = [t.key for t in test_results]

        self.worker_mgr.start_worker(
            WorkerType.AddTestsToExecution,
            [test_execution_key for _ in range(len(test_keys))],
            test_keys,
        )
        self._update_test_plan_execution_status(test_execution_key)
        if clean_obsolete:
            self._clean_test_execution(test_execution_key)

        if test_plan_key:
            self.worker_mgr.start_worker(
                WorkerType.AddTestsToPlan,
                [test_plan_key for _ in range(len(test_keys))],
                test_keys,
            )
            logger.info(
                f"Start adding test execution {test_execution_key} to test plan {test_plan_key}"
            )
            # self.context.xray.update_test_plan_test_executions(
            #     test_plan_key, add=[test_execution_key]
            # )
            # workaround for xray testplan API performance issue
            self.context.jira.update_issue_field(
                test_execution_key,
                fields={self.config.cf_id_test_plan: [test_plan_key]},
            )
            self._update_test_plan_execution_status(test_plan_key)
            if clean_obsolete:
                self._clean_test_plan(test_plan_key)

        # update test execution result
        self.worker_mgr.start_worker(
            WorkerType.UpdateTestResults,
            test_results,
            [test_execution_key for _ in range(len(test_results))],
        )

    def upload_test_results(
        self,
        test_plan_name: str,
        test_execution_name: str,
        test_results: List[TestResultEntity],
        clean_obsolete: bool = False,
        full_test_set: bool = False,
        ignore_missing: bool = False,
    ) -> Tuple[str, str]:
        test_plan_key = self.worker_mgr.api_wrapper.create_test_plan(test_plan_name)
        test_execution_key = self.worker_mgr.api_wrapper.create_test_execution(
            test_execution_name
        )
        self.upload_test_results_by_key(
            test_execution_key=test_execution_key,
            test_plan_key=test_plan_key,
            test_results=test_results,
            clean_obsolete=clean_obsolete,
            full_test_set=full_test_set,
            ignore_missing=ignore_missing,
        )
        return test_plan_key, test_execution_key

    def sync_check(self, local_tests: List[TestEntity]):
        """
        1. make sure all local tests have been marked with keys
        2. make sure all the uniqueness of local tests keys and unique identifiers
        3. make sure requirement keys are valid
        4. make sure defect keys are valid
        """
        test_keys = [_.key for _ in local_tests]
        req_keys_list = [_.req_keys for _ in local_tests]
        defect_keys_list = [_.defect_keys for _ in local_tests]
        assert (
            None not in test_keys
        ), "Some of the tests are not marked with test key, run sync prepare firstly."

        self._check_tests_uniqueness(
            local_tests, "Duplicated key/unique_identifier found in local tests"
        )

        def chunks(xs, n=20):
            n = max(1, n)
            return list(xs[i : i + n] for i in range(0, len(xs), n))

        results = self.worker_mgr.start_worker(
            WorkerType.BulkGetJiraDetails, chunks(test_keys)
        )
        errors = []
        test_details_list = []
        for result in results:
            if not result.success:
                errors.append(f"Query test from jira failed: {result}")
            else:
                test_details_list.extend(result.data)
        for test_key, _, issue_type in test_details_list:
            if issue_type != "Test":
                errors.append(f"{test_key} is not a test at all.")

        merged_link_keys = []
        for req_keys in req_keys_list:
            if req_keys:
                merged_link_keys.extend(req_keys)
        for defect_keys in defect_keys_list:
            if defect_keys:
                merged_link_keys.extend(defect_keys)
        merged_link_keys = list(set(merged_link_keys))
        results = self.worker_mgr.start_worker(
            WorkerType.BulkGetJiraDetails, chunks(merged_link_keys)
        )
        link_details_list = []
        for result in results:
            if not result.success:
                errors.append(f"Query test links from jira failed: {result}")
            else:
                link_details_list.extend(result.data)
            for issue_key, _, issue_type in link_details_list:
                if issue_type == "Test":
                    errors.append(f"Test link {issue_key} should not be a test.")

        if errors:
            err_msg = ""
            for idx, err in enumerate(errors):
                err_msg = f"{err_msg}\n({idx + 1}) {err}"
            raise AssertionError(f"Found following errors:{err_msg}")
