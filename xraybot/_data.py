from dataclasses import dataclass, field
from enum import Enum
from typing import List, Any, Optional


@dataclass
class WorkerResult:
    success: bool
    data: Any


@dataclass
class TestEntity:
    key: Optional[str]
    summary: str
    unique_identifier: str
    description: str = ""
    repo_path: List[str] = field(default_factory=list)
    labels: List[str] = field(default_factory=list)
    req_keys: List[str] = field(default_factory=list)
    defect_keys: List[str] = field(default_factory=list)

    def __eq__(self, other):
        if isinstance(other, TestEntity):
            return (
                self.key == other.key
                and self.summary == other.summary
                and self.unique_identifier == other.unique_identifier
                and self.description == other.description
                and self.repo_path == other.repo_path
                and set(self.labels) == set(other.labels)
                and set(self.req_keys) == set(other.req_keys)
                and set(self.defect_keys) == set(other.defect_keys)
            )
        else:
            return False


class XrayResultType(Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    TODO = "TODO"
    EXECUTING = "EXECUTING"


@dataclass
class TestResultEntity:
    key: str
    result: XrayResultType
