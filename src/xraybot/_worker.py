import requests
from functools import lru_cache
from abc import abstractmethod
from enum import Enum
import json
from typing import List, Optional, Tuple
from retry import retry
from atlassian.rest_client import HTTPError
from concurrent.futures import ThreadPoolExecutor
from ._data import TestEntity, WorkerResult, TestResultEntity
from ._utils import logger, build_repo_hierarchy, dict_to_graphql_param
from ._context import XrayBotContext
from functools import cached_property


class _XrayAPIWrapper:
    def __init__(self, context: XrayBotContext):
        self.context = context

    def prepare_repo_folder_hierarchy(self, test_entities: List[TestEntity]):
        self.init_automation_folder()
        repo_hierarchy = build_repo_hierarchy([t.repo_path for t in test_entities])

        def iter_repo_hierarchy(parent_paths: List[str], root: List[dict]):
            for node in root:
                folder_name = node["name"]
                cur_paths = parent_paths + [folder_name]
                self.create_repo_folder("/".join(cur_paths))
                sub_folders = node["folders"]
                if sub_folders:
                    iter_repo_hierarchy(cur_paths, sub_folders)

        iter_repo_hierarchy(
            [f"/{self.context.config.automation_folder_name}"], repo_hierarchy
        )

    def init_automation_folder(self):
        self.create_repo_folder(self.context.config.automation_folder_name)
        self.create_repo_folder(
            f"{self.context.config.automation_folder_name}/{self.context.config.obsolete_automation_folder_name}"
        )
        # ensure all_folders is refreshed
        del self.all_folders

    def get_xray_tests_by_repo_folder(
        self, repo_folder: str, customized_field_jql: str = ""
    ):
        jql = f"project = '{self.context.project_key}' and type = 'Test' and status != 'Obsolete' and reporter = '{self.context.jira_username}'{customized_field_jql}"
        get_test_param = f'jql: "{jql}", testType: {{name: "Automated"}}, folder: {{path: "/{repo_folder}", includeDescendants: true}}, projectId: "{self.context.project_id}"'
        payload = f"""
        {{
            getTests({get_test_param}, limit: 1) {{
                total
            }}
        }}
        """
        total = self.context.execute_xray_graphql(payload)["getTests"]["total"]
        pages = total // 100 + 1
        # max 25 resolvers, each query contains 3 resolvers, so it will have 8 batch queries
        max_resovler_batch = 8
        all_results: List[dict] = []

        def _worker(batch_start):
            batch_end = min(batch_start + max_resovler_batch, pages)
            batch_payload = ""
            for page in range(batch_start, batch_end):
                logger.debug(f"Start getting tests from page {page} to {batch_end}")
                each_page_payload = f"""
                query{page}: getTests({get_test_param}, limit: 100, start: {page * 100}) {{
                    results {{
                        issueId
                        unstructured
                        folder {{
                            path
                        }}
                        jira(fields: ["key", "summary", "description", "labels", "issuelinks"])
                    }}
                }}
                """
                batch_payload = batch_payload + each_page_payload
            batch_payload = f"{{{batch_payload}}}"
            batch_results = self.context.execute_xray_graphql(batch_payload)
            return list(batch_results.values())

        with ThreadPoolExecutor() as executor:
            all_results.extend(
                executor.map(_worker, range(0, pages, max_resovler_batch))
            )
        return sum([_["results"] for _ in sum([_ for _ in all_results], [])], [])

    @cached_property
    def all_folders(self):
        logger.info(
            f"Start getting all test folders for project: {self.context.project_key}"
        )
        query = f"""
        {{
            getFolder(projectId: "{self.context.project_id}", path: "/") {{
                name
                path
                testsCount
                folders
            }}
        }}
        """
        return self.context.execute_xray_graphql(query)["getFolder"]

    def remove_links(self, test_entity: TestEntity):
        issue = self.context.jira.get_issue(test_entity.key)
        for link in issue["fields"]["issuelinks"]:
            if link["type"]["name"] in ("Test", "Defect"):
                self.context.jira.remove_issue_link(link["id"])

    def link_test(self, test_entity: TestEntity):
        for req_key in test_entity.req_keys:
            logger.info(
                f"Start linking test {test_entity.key} to requirement: {req_key}"
            )
            link_param = {
                "type": {"name": "Test"},
                "inwardIssue": {"key": test_entity.key},
                "outwardIssue": {"key": req_key},
            }
            try:
                self.context.jira.create_issue_link(link_param)
            except Exception as e:
                raise AssertionError(
                    f"Link requirement {req_key} with error: {e}"
                ) from e
        for defect_key in test_entity.defect_keys:
            logger.info(f"Start linking test {test_entity.key} to defect: {defect_key}")
            link_param = {
                "type": {"name": "Defect"},
                "inwardIssue": {"key": test_entity.key},
                "outwardIssue": {"key": defect_key},
            }
            try:
                self.context.jira.create_issue_link(link_param)
            except Exception as e:
                raise AssertionError(f"Link defect {defect_key} with error: {e}") from e

    @retry(tries=10, delay=3, logger=logger)
    def move_test_folder(self, test_entity: TestEntity):
        assert test_entity.issue_id is not None, "Test entity issue id cannot be None"
        folder_path = "/".join(
            [self.context.config.automation_folder_name] + test_entity.repo_path
        )
        payload = f"""
        mutation {{
            updateTestFolder(
                issueId: "{test_entity.issue_id}",
                folderPath: "{folder_path}"
            )
        }}
        """
        self.context.execute_xray_graphql(payload)

    def create_repo_folder(self, folder_path: str):
        folder_path = (
            f"/{folder_path}" if not folder_path.startswith("/") else folder_path
        )

        def _is_folder_path_existing(folders: List[dict]) -> bool:
            for folder in folders:
                if folder["path"] == folder_path:
                    return True
                if folder.get("folders"):
                    if _is_folder_path_existing(folder["folders"]):
                        return True
            return False

        if not _is_folder_path_existing(self.all_folders["folders"]):
            payload = f"""
                    mutation {{
                        createFolder(
                            projectId: "{self.context.project_id}",
                            path: "{folder_path}"
                        ) {{
                            folder {{
                                name
                                path
                                testsCount
                            }}
                            warnings
                        }}
                    }}
                    """
            logger.info(f"Start creating repo folder: {folder_path}")
            self.context.execute_xray_graphql(payload)["createFolder"]
        else:
            logger.info(f"Using existing folder: {folder_path}")

    def finalize_test_from_any_status(self, test_entity: TestEntity):
        logger.info(f"Start finalizing test: {test_entity.key}")
        status = self.context.jira.get_issue_status(test_entity.key)
        if status == "Finalized":
            return

        for status in ["In-Draft", "Ready for Review", "In Review", "Finalized"]:
            try:
                self.context.jira.set_issue_status(test_entity.key, status)
            except Exception as e:
                # ignore errors from any status
                logger.debug(f"Finalize test with error: {e}")

        status = self.context.jira.get_issue_status(test_entity.key)
        assert status == "Finalized", f"Test {test_entity.key} cannot be finalized."

    def renew_test_details(self, marked_test: TestEntity):
        logger.info(f"Start renewing external marked test: {marked_test.key}")
        assert marked_test.key is not None, "Marked test key cannot be None"
        result = self.context.jira.get_issue(
            marked_test.key, fields=("project", "issuetype", "status")
        )
        marked_test.issue_id = result["id"]
        assert result["fields"]["project"]["key"] == self.context.project_key, (
            f"Marked test {marked_test.key} is not belonging to current project."
        )
        assert result["fields"]["issuetype"]["name"] == "Test", (
            f"Marked test {marked_test.key} is not a test at all."
        )
        fields = {
            "description": marked_test.description,
            "summary": marked_test.summary,
            "assignee": {"accountId": self.context.jira_account_id},
            "reporter": {"accountId": self.context.jira_account_id},
            "labels": marked_test.labels,
            **self.context.config.get_tests_custom_fields_payload(),
        }
        self.context.jira.update_issue_field(
            key=marked_test.key,
            fields=fields,
        )
        self.update_test_type(marked_test)
        self.update_unstructured_test_definition(marked_test)

    def update_test_type(self, test_entity: TestEntity):
        logger.info(f"Start updating test type: {test_entity.key}")
        assert test_entity.issue_id is not None, "Test entity issue id cannot be None"
        payload = f"""
        mutation {{
            updateTestType(issueId: "{test_entity.issue_id}", testType: {{ name: "Automated" }}) {{
                issueId
            }}
        }}
        """
        self.context.execute_xray_graphql(payload)

    def update_unstructured_test_definition(self, test_entity: TestEntity):
        logger.info(f"Start updating unstructured test definition: {test_entity.key}")
        assert test_entity.issue_id is not None, "Test entity issue id cannot be None"
        payload = f"""
        mutation {{
            updateUnstructuredTestDefinition(issueId: "{test_entity.issue_id}", unstructured: "{test_entity.unique_identifier}" ) {{
                issueId
                unstructured
            }}
        }}
        """
        self.context.execute_xray_graphql(payload)

    def create_test_plan(self, test_plan_name: str) -> str:
        jql = f"project='{self.context.project_key}' and reporter='{self.context.jira_username}'"
        payload = f"""
        {{
            getTestPlans(jql: "{jql}", limit: 1) {{
                total
            }}
        }}
        """
        total = self.context.execute_xray_graphql(payload)["getTestPlans"]["total"]
        pages = total // 100 + 1
        max_resovler_batch = 8
        all_results: List[dict] = []

        def _worker(batch_start):
            batch_end = min(batch_start + max_resovler_batch, pages)
            batch_payload = ""
            for page in range(batch_start, batch_end):
                batch_payload = (
                    batch_payload
                    + f"""
                query{page}: getTestPlans(jql: "{jql}", limit: 100, start: {page * 100}) {{
                    results {{
                        issueId
                        jira(fields: ["key", "summary"])
                    }}
                }}
                """
                )
            batch_payload = f"{{{batch_payload}}}"
            batch_results = self.context.execute_xray_graphql(batch_payload)
            return list(batch_results.values())

        with ThreadPoolExecutor() as executor:
            all_results.extend(
                executor.map(_worker, range(0, pages, max_resovler_batch))
            )
        all_test_plans: List[dict] = sum(
            [_["results"] for _ in sum([_ for _ in all_results], [])], []
        )
        for test_plan in all_test_plans:
            if test_plan["jira"]["summary"] == test_plan_name:
                key = test_plan["jira"]["key"]
                logger.info(f"Found existing test plan: {key}")
                return key

        fields = {
            "project": {"key": self.context.project_key},
            "summary": test_plan_name,
            "assignee": {"accountId": self.context.jira_account_id},
        }

        payload = f"""
        mutation {{
            createTestPlan(jira: {{ fields: {dict_to_graphql_param(fields)} }}) {{
                testPlan {{
                    issueId
                    jira(fields: ["key"])
                }}
            }}
        }}
        """
        result = self.context.execute_xray_graphql(payload)["createTestPlan"][
            "testPlan"
        ]
        test_plan_key = result["jira"]["key"]
        logger.info(f"Created new test plan: {test_plan_key}")
        return test_plan_key

    def create_test_execution(self, test_execution_name: str) -> str:
        jql = f"project='{self.context.project_key}' and reporter='{self.context.jira_username}'"
        payload = f"""
        {{
            getTestExecutions(jql: "{jql}", limit: 1) {{
                total
            }}
        }}
        """
        total = self.context.execute_xray_graphql(payload)["getTestExecutions"]["total"]
        pages = total // 100 + 1
        max_resovler_batch = 8
        all_results: List[dict] = []

        def _worker(batch_start):
            batch_end = min(batch_start + max_resovler_batch, pages)
            batch_payload = ""
            for page in range(batch_start, batch_end):
                batch_payload = (
                    batch_payload
                    + f"""
                query{page}: getTestExecutions(jql: "{jql}", limit: 100, start: {page * 100}) {{
                    results {{
                        issueId
                        jira(fields: ["key", "summary"])
                    }}
                }}
                """
                )
            batch_payload = f"{{{batch_payload}}}"
            batch_results = self.context.execute_xray_graphql(batch_payload)
            return list(batch_results.values())

        with ThreadPoolExecutor() as executor:
            all_results.extend(
                executor.map(_worker, range(0, pages, max_resovler_batch))
            )
        all_test_executions: List[dict] = sum(
            [_["results"] for _ in sum([_ for _ in all_results], [])], []
        )
        for test_execution in all_test_executions:
            if test_execution["jira"]["summary"] == test_execution_name:
                key = test_execution["jira"]["key"]
                logger.info(f"Found existing test execution: {key}")
                return key

        fields = {
            "project": {"key": self.context.project_key},
            "summary": test_execution_name,
            "assignee": {"accountId": self.context.jira_account_id},
        }

        payload = f"""
        mutation {{
            createTestExecution(jira: {{ fields: {dict_to_graphql_param(fields)} }}) {{
                testExecution {{
                    issueId
                    jira(fields: ["key"])
                }}
            }}
        }}
        """
        result = self.context.execute_xray_graphql(payload)["createTestExecution"][
            "testExecution"
        ]
        test_execution_key = result["jira"]["key"]
        logger.info(f"Created new test execution: {test_execution_key}")
        return test_execution_key

    def get_tests_from_test_plan(self, test_plan_key) -> List[dict]:
        test_plan_issue_id = self.get_issue_id_by_key(test_plan_key)
        resp = self.context.execute_xray_graphql(f"""
        {{
            getTestPlan(issueId: "{test_plan_issue_id}") {{
                tests(limit: 1) {{
                    total
                }}
            }}
        }}
        """)
        total = resp["getTestPlan"]["tests"]["total"]
        pages = total // 100 + 1
        max_resovler_batch = 8
        all_results: List[dict] = []

        def _worker(batch_start):
            batch_end = min(batch_start + max_resovler_batch, pages)
            batch_payload = ""
            for page in range(batch_start, batch_end):
                batch_payload = (
                    batch_payload
                    + f"""
                query{page}: getTestPlan(issueId: "{test_plan_issue_id}") {{
                    tests(limit: 100, start: {page * 100}) {{
                        results {{
                            issueId,
                            jira(fields: ["key", "status"])
                        }}
                    }}
                }}
                """
                )
            batch_payload = f"{{{batch_payload}}}"
            batch_results = self.context.execute_xray_graphql(batch_payload)
            return list(batch_results.values())

        with ThreadPoolExecutor() as executor:
            all_results.extend(
                executor.map(_worker, range(0, pages, max_resovler_batch))
            )
        return sum(
            [_["tests"]["results"] for _ in sum([_ for _ in all_results], [])], []
        )

    def get_tests_from_test_execution(self, test_execution_key) -> List[dict]:
        test_execution_issue_id = self.get_issue_id_by_key(test_execution_key)
        resp = self.context.execute_xray_graphql(f"""
        {{
            getTestExecution(issueId: "{test_execution_issue_id}") {{
                tests(limit: 1) {{
                    total
                }}
            }}
        }}
        """)
        total = resp["getTestExecution"]["tests"]["total"]
        pages = total // 100 + 1
        max_resovler_batch = 8
        all_results: List[dict] = []

        def _worker(batch_start):
            batch_end = min(batch_start + max_resovler_batch, pages)
            batch_payload = ""
            for page in range(batch_start, batch_end):
                batch_payload = (
                    batch_payload
                    + f"""
                query{page}: getTestExecution(issueId: "{test_execution_issue_id}") {{
                    tests(limit: 100, start: {page * 100}) {{
                        results {{
                            issueId,
                            jira(fields: ["key", "status"])
                        }}
                    }}
                }}
                """
                )
            batch_payload = f"{{{batch_payload}}}"
            batch_results = self.context.execute_xray_graphql(batch_payload)
            return list(batch_results.values())

        with ThreadPoolExecutor() as executor:
            all_results.extend(
                executor.map(_worker, range(0, pages, max_resovler_batch))
            )
        return sum(
            [_["tests"]["results"] for _ in sum([_ for _ in all_results], [])], []
        )

    def add_test_environments_to_test_execution(
        self, test_execution_key: str, test_environments: List[str]
    ):
        logger.info(
            f"Start adding test environments: {test_environments} to test execution: {test_execution_key}"
        )
        test_execution_issue_id = self.get_issue_id_by_key(test_execution_key)
        test_environments_param = (
            ",".join([f'"{_}"' for _ in test_environments])
            if test_environments
            else "[]"
        )
        payload = f"""
        mutation {{
            addTestEnvironmentsToTestExecution(issueId: "{test_execution_issue_id}", testEnvironments: {test_environments_param}) {{
                warning
            }}
        }}
        """
        self.context.execute_xray_graphql(payload)

    def fuzzy_update(self, jira_key: str, payload: dict):
        fields = {}
        for k, v in payload.items():
            custom_field = self.context.config.get_custom_field_by_name(k)
            k = custom_field if custom_field is not None else k
            fields[k] = v
        try:
            self.context.jira.update_issue_field(
                key=jira_key,
                fields=fields,
            )
        except HTTPError as e:
            logger.error(f"Update failed with error: {e.response.text}")

    def delete_folder(self, path: str):
        try:
            payload = f"""
            mutation {{
                deleteFolder(projectId: "{self.context.project_id}", path: "{path}")
            }}
            """
            self.context.execute_xray_graphql(payload)
        except HTTPError as e:
            # parent folder could be deleted by other worker
            # ignore such errors
            logger.warning(f"Ignore errors: {e}")

    def get_all_empty_folders(self) -> List[str]:
        obsolete_folder_path = f"/{self.context.config.automation_folder_name}/{self.context.config.obsolete_automation_folder_name}"

        def _iter_folders(_folders: List[dict]):
            _result: List[str] = []
            for _folder in _folders:
                sub_folders = _folder["folders"]
                tests_count = _folder["testsCount"]
                folder_path = _folder["path"]
                if sub_folders:
                    _result = _result + _iter_folders(sub_folders)
                elif tests_count == 0 and folder_path != obsolete_folder_path:
                    _result.append(folder_path)
            return _result

        for folder in self.all_folders["folders"]:
            if folder["name"] == self.context.config.automation_folder_name:
                return _iter_folders(folder["folders"])
        return []

    @lru_cache
    def get_issue_id_by_key(self, key: str) -> str:
        logger.info(f"Start getting issue id by key: {key}")
        return self.context.jira.get_issue(key, fields=["id"])["id"]

    def add_tests_to_test_execution(
        self, test_execution_issue_id: str, test_issue_ids: List[str]
    ):
        test_issue_ids_param = ",".join([f'"{_}"' for _ in test_issue_ids])
        payload = f"""
        mutation {{
            addTestsToTestExecution(issueId: "{test_execution_issue_id}", testIssueIds: [{test_issue_ids_param}]) {{
                warning
            }}
        }}
        """
        self.context.execute_xray_graphql(payload)

    def add_tests_to_test_plan(
        self, test_plan_issue_id: str, test_issue_ids: List[str]
    ):
        test_issue_ids_param = ",".join([f'"{_}"' for _ in test_issue_ids])
        payload = f"""
        mutation {{
            addTestsToTestPlan(issueId: "{test_plan_issue_id}", testIssueIds: [{test_issue_ids_param}]) {{
                warning
            }}
        }}
        """
        self.context.execute_xray_graphql(payload)

    def add_test_execution_to_test_plan(
        self, test_plan_key: str, test_execution_key: str
    ):
        test_plan_issue_id = self.get_issue_id_by_key(test_plan_key)
        test_execution_issue_id = self.get_issue_id_by_key(test_execution_key)
        payload = f"""
        mutation {{
            addTestExecutionsToTestPlan(issueId: "{test_plan_issue_id}", testExecIssueIds: "{test_execution_issue_id}") {{
                warning
            }}
        }}
        """
        self.context.execute_xray_graphql(payload)


class _XrayBotWorker:
    def __init__(self, api_wrapper: _XrayAPIWrapper):
        self.api_wrapper = api_wrapper
        self.context = self.api_wrapper.context

    @abstractmethod
    def run(self, *args, **kwargs):
        pass


class _ObsoleteTestWorker(_XrayBotWorker):
    def run(self, test_entity: TestEntity):
        logger.info(f"Start obsoleting test: {test_entity.key}")
        self.context.jira.set_issue_status(test_entity.key, "Obsolete")
        self.api_wrapper.remove_links(test_entity)
        # set current test repo path to `Obsolete` folder
        test_entity.repo_path = [self.context.config.obsolete_automation_folder_name]
        self.api_wrapper.move_test_folder(test_entity)


class _DraftTestCreateWorker(_XrayBotWorker):
    def run(self, test_entity: TestEntity):
        logger.info(f"Start creating test draft: {test_entity.summary}")

        fields = {
            "issuetype": {"name": "Test"},
            "project": {"key": self.context.project_key},
            "description": test_entity.description,
            "summary": f"[🤖Automation Draft] {test_entity.summary}",
            "assignee": {"accountId": self.context.jira_account_id},
            "reporter": {"accountId": self.context.jira_account_id},
            **self.context.config.get_tests_custom_fields_payload(),
        }
        fields_param = dict_to_graphql_param(fields, multilines_keys=["description"])

        payload = f"""
        mutation {{
          createTest(
            testType: {{ name: "Automated" }},
            unstructured: "{test_entity.unique_identifier}",
            jira: {{
              fields: {fields_param}
            }}
          ) {{
            test {{
              issueId
              jira(fields: ["key"])
            }}
          }}
        }}
        """
        result = self.context.execute_xray_graphql(payload)["createTest"]["test"]
        test_entity.key = result["jira"]["key"]
        test_entity.issue_id = result["issueId"]
        logger.info(f"Created xray test draft: {test_entity.key}")
        return test_entity


class _ExternalMarkedTestUpdateWorker(_XrayBotWorker):
    def run(self, test_entity: TestEntity):
        logger.info(f"Start updating external marked test: {test_entity.key}")
        self.api_wrapper.renew_test_details(test_entity)
        self.api_wrapper.finalize_test_from_any_status(test_entity)
        self.api_wrapper.remove_links(test_entity)
        self.api_wrapper.link_test(test_entity)
        self.api_wrapper.move_test_folder(test_entity)


class _InternalMarkedTestUpdateWorker(_XrayBotWorker):
    def run(self, test_entity: TestEntity):
        logger.info(f"Start updating internal marked test: {test_entity.key}")
        assert test_entity.key is not None, "Jira test key cannot be None"
        fields = {
            "summary": test_entity.summary,
            "description": test_entity.description,
            "labels": test_entity.labels,
        }
        self.context.jira.update_issue_field(
            key=test_entity.key,
            fields=fields,
        )
        self.api_wrapper.update_unstructured_test_definition(test_entity)
        self.api_wrapper.remove_links(test_entity)
        self.api_wrapper.link_test(test_entity)
        self.api_wrapper.move_test_folder(test_entity)


class _AddTestsToPlanWorker(_XrayBotWorker):
    def run(
        self, test_plan_key: str, test_key_and_ids: List[Tuple[str, Optional[str]]]
    ):
        logger.info(f"Start adding tests to test plan: {test_plan_key}")
        test_issue_ids = []
        for test_key, issue_id in test_key_and_ids:
            if issue_id is None:
                issue_id = self.api_wrapper.get_issue_id_by_key(test_key)
            test_issue_ids.append(issue_id)
        test_plan_issue_id = self.api_wrapper.get_issue_id_by_key(test_plan_key)
        self.api_wrapper.add_tests_to_test_plan(test_plan_issue_id, test_issue_ids)


class _AddTestsToExecutionWorker(_XrayBotWorker):
    def run(
        self, test_execution_key: str, test_key_and_ids: List[Tuple[str, Optional[str]]]
    ):
        logger.info(f"Start adding tests to test execution: {test_execution_key}")
        test_issue_ids = []
        for test_key, issue_id in test_key_and_ids:
            if issue_id is None:
                issue_id = self.api_wrapper.get_issue_id_by_key(test_key)
            test_issue_ids.append(issue_id)
        test_execution_issue_id = self.api_wrapper.get_issue_id_by_key(
            test_execution_key
        )
        self.api_wrapper.add_tests_to_test_execution(
            test_execution_issue_id, test_issue_ids
        )


class _UpdateTestResultsWorker(_XrayBotWorker):
    def run(self, test_execution_key: str, test_results: List[TestResultEntity]):
        logger.info(f"Start updating test results: {test_execution_key}")
        headers = {
            "Authorization": f"Bearer {self.context._xray_api_token}",
            "Content-Type": "application/json",
        }
        tests = [
            {
                "testKey": t.key,
                "status": t.result.value,
            }
            for t in test_results
        ]
        payload = {"testExecutionKey": test_execution_key, "tests": tests}
        r = requests.post(
            f"{self.context._xray_url}/import/execution",
            headers=headers,
            data=json.dumps(payload),
            timeout=10 * 60,
        )
        r.raise_for_status()
        return r.json()


class _CleanTestExecutionWorker(_XrayBotWorker):
    def run(self, test_execution_key: str, test_execution_tests: List[dict]):
        test_execution_issue_id = self.api_wrapper.get_issue_id_by_key(
            test_execution_key
        )
        to_be_deleted_test_issue_ids = [
            _["issueId"]
            for _ in test_execution_tests
            if _["jira"]["status"]["name"] != "Finalized"
        ]
        if to_be_deleted_test_issue_ids:
            to_be_deleted_test_issue_ids_param = ",".join(
                [f'"{_}"' for _ in to_be_deleted_test_issue_ids]
            )
            logger.info(
                f"Start removing tests from test execution: {to_be_deleted_test_issue_ids_param}"
            )
            payload = f"""
            mutation {{
                removeTestsFromTestExecution(issueId: "{test_execution_issue_id}", testIssueIds: [{to_be_deleted_test_issue_ids_param}])
            }}
            """
            self.context.execute_xray_graphql(payload)


class _CleanTestPlanWorker(_XrayBotWorker):
    def run(self, test_plan_key: str, test_plan_tests: List[dict]):
        test_plan_issue_id = self.api_wrapper.get_issue_id_by_key(test_plan_key)
        to_be_deleted_test_issue_ids = [
            _["issueId"]
            for _ in test_plan_tests
            if _["jira"]["status"]["name"] != "Finalized"
        ]
        if to_be_deleted_test_issue_ids:
            to_be_deleted_test_issue_ids_param = ",".join(
                [f'"{_}"' for _ in to_be_deleted_test_issue_ids]
            )
            logger.info(
                f"Start removing tests from test plan: {to_be_deleted_test_issue_ids_param}"
            )
            payload = f"""
            mutation {{
                removeTestsFromTestPlan(issueId: "{test_plan_issue_id}", testIssueIds: [{to_be_deleted_test_issue_ids_param}])
            }}
            """
            self.context.execute_xray_graphql(payload)


class _BulkGetJiraDetailsWorker(_XrayBotWorker):
    def run(self, jira_keys: List[str]):
        logger.info(f"Bulk checking jira keys: {jira_keys}...")
        results = self.context.jira.bulk_issue(jira_keys, fields="status,issuetype")
        results = [
            (
                issue["key"],
                issue["fields"]["status"]["name"],
                issue["fields"]["issuetype"]["name"],
            )
            for issue in results[0]["issues"]
        ]
        non_existing_keys = set(jira_keys) - set([_[0] for _ in results])
        assert not non_existing_keys, (
            f"Non existing jira key found: {non_existing_keys}"
        )
        return results


class _CleanRepoFolderWorker(_XrayBotWorker):
    def run(self, folder_path: str):
        logger.info(f"Start deleting empty folder: {folder_path}")
        self.api_wrapper.delete_folder(folder_path)


class WorkerType(Enum):
    ObsoleteTest = _ObsoleteTestWorker
    ExternalMarkedTestUpdate = _ExternalMarkedTestUpdateWorker
    InternalMarkedTestUpdate = _InternalMarkedTestUpdateWorker
    AddTestsToPlan = _AddTestsToPlanWorker
    AddTestsToExecution = _AddTestsToExecutionWorker
    UpdateTestResults = _UpdateTestResultsWorker
    CleanTestExecution = _CleanTestExecutionWorker
    CleanTestPlan = _CleanTestPlanWorker
    BulkGetJiraDetails = _BulkGetJiraDetailsWorker
    DraftTestCreate = _DraftTestCreateWorker
    CleanRepoFolder = _CleanRepoFolderWorker


class XrayBotWorkerMgr:
    def __init__(self, context: XrayBotContext):
        self.context = context
        self.api_wrapper = _XrayAPIWrapper(self.context)

    @staticmethod
    def _worker_wrapper(worker_func, *iterables) -> WorkerResult:
        try:

            @retry(tries=3, delay=1, logger=logger)
            def run_with_retry():
                ret = worker_func(*iterables)
                return WorkerResult(success=True, data=ret)

            return run_with_retry()
        except Exception as e:
            logger.info(
                f"Worker [{worker_func.__qualname__.split('.')[0].lstrip('_')}] raised error: {e}"
            )
            converted = [str(_) for _ in iterables]
            err_msg = f"❌{e} -> 🐛{' | '.join(converted)}"
            return WorkerResult(success=False, data=err_msg)

    def start_worker(self, worker_type: WorkerType, *iterables) -> List[WorkerResult]:
        worker: _XrayBotWorker = worker_type.value(self.api_wrapper)
        with ThreadPoolExecutor(self.context.config.worker_num) as executor:
            results = executor.map(
                self._worker_wrapper,
                [worker.run for _ in range(len(iterables[0]))],
                *iterables,
            )
            return list(results)
