from dataclasses import dataclass
from enum import Enum
from typing import Optional


@dataclass
class TestEntity:
    # store in test custom field "Generic Test Definition"
    # using as the unique identified for one certain test
    unique_identifier: str
    summary: str
    description: str
    req_key: str
    key: Optional[str] = None


class XrayResultType(Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    TODO = "TODO"


@dataclass
class TestResultEntity:
    key: str
    result: XrayResultType
