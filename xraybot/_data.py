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
    labels: List[str] = field(default_factory=list)
    req_keys: List[str] = field(default_factory=list)
    defect_keys: List[str] = field(default_factory=list)


class XrayResultType(Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    TODO = "TODO"


@dataclass
class TestResultEntity:
    key: str
    result: XrayResultType
