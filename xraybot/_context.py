from typing import List, Union, Dict, Optional
from atlassian import Jira
import requests
import json

class _XrayBotConfig:
    def __init__(self, jira):
        self._jira = jira
        self._custom_fields: Dict[str, Union[str, List[str]]] = {}
        self._cached_all_custom_fields = None
        self._query_page_limit: int = 100
        self._worker_num: int = 30
        self._automation_folder_name = "Automation Test"
        self._obsolete_automation_folder_name = "Obsolete"

    def configure_query_page_limit(self, limit: int):
        self._query_page_limit = limit

    def configure_worker_num(self, worker_num: int):
        self._worker_num = worker_num

    def configure_automation_folder_name(self, folder_name: str):
        self._automation_folder_name = folder_name

    def configure_obsolete_automation_folder_name(self, folder_name: str):
        self._obsolete_automation_folder_name = folder_name

    @property
    def query_page_limit(self) -> int:
        return self._query_page_limit

    @property
    def worker_num(self) -> int:
        return self._worker_num

    @property
    def automation_folder_name(self) -> str:
        return self._automation_folder_name

    @property
    def obsolete_automation_folder_name(self) -> str:
        return self._obsolete_automation_folder_name

    @property
    def custom_fields(self):
        return self._custom_fields

    def configure_custom_field(
        self, field_name: str, field_value: Union[str, List[str]]
    ):
        """
        :param field_name: str, custom field name
        :param field_value: custom field value of the test ticket
        e.g: field_value="value", field_value=["value1", "value2"]
        """
        self._custom_fields[field_name] = field_value

    def get_custom_field_by_name(self, name: str):
        if not self._cached_all_custom_fields:
            self._cached_all_custom_fields = self._jira.get_all_custom_fields()
        for f in self._cached_all_custom_fields:
            if f["name"] == name:
                return f["id"]

    def get_tests_custom_fields_payload(self):
        fields = dict()
        for k, v in self._custom_fields.items():
            custom_field = self.get_custom_field_by_name(k)
            if isinstance(v, list) and v:
                fields[custom_field] = [{"value": _} for _ in v]
            else:
                fields[custom_field] = {"value": v}
        return fields


class XrayBotContext:
    def __init__(
        self,
        jira_url: str,
        jira_username: str,
        jira_pwd: str,
        project_key: str,
        timeout: int,
        xray_api_token: str,
    ):
        self._jira: Jira = Jira(
            url=jira_url, username=jira_username, password=jira_pwd, timeout=timeout
        )
        self._xray_session = requests.Session()
        self._xray_api_token = xray_api_token
        self._xray_session.headers.update({
            "Authorization": f"Bearer {self._xray_api_token}",
            "Content-Type": "application/json"
        })
        self._project_key: str = project_key
        self._project_id: Optional[int] = None
        self._config = _XrayBotConfig(self._jira)
    
    def execute_xray_graphql(self, payload):
        """Execute a GraphQL query or mutation"""
        url = "https://xray.cloud.getxray.app/api/v2/graphql"
        response = self._xray_session.post(url, json={"query": payload})
        response.raise_for_status()
        result = response.json()
        if "errors" in result:
            raise AssertionError(f"GraphQL error: {json.dumps(result['errors'], indent=2)}")
        return result["data"]

    @property
    def jira_username(self) -> Jira:
        return self._jira.username

    @property
    def jira(self) -> Jira:
        return self._jira

    @property
    def project_key(self) -> str:
        return self._project_key

    @property
    def config(self) -> _XrayBotConfig:
        return self._config

    @property
    def project_id(self) -> int:
        if self._project_id is None:
            self._project_id = self._jira.get_project(self._project_key)["id"]
        return self._project_id
