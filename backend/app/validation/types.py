"""
Shared types for the validation layer.

Every check returns a CheckResult — a small dataclass with pass/fail,
severity, human-readable message, and optionally the field name it targets.

The validate node collects all CheckResults and writes them to the DB.
The routing rules engine then reads aggregated counts (error_count,
warning_count) to make routing decisions.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class CheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"


@dataclass
class CheckResult:
    check_name: str
    status: CheckStatus
    severity: Severity
    message: str
    field_name: Optional[str] = None

    @property
    def passed(self) -> bool:
        return self.status == CheckStatus.PASS

    @property
    def failed(self) -> bool:
        return self.status == CheckStatus.FAIL

    def to_dict(self) -> dict:
        return {
            "check_name": self.check_name,
            "status": self.status,
            "severity": self.severity,
            "message": self.message,
            "field_name": self.field_name,
        }


def pass_check(name: str, message: str, field_name: Optional[str] = None) -> CheckResult:
    return CheckResult(
        check_name=name,
        status=CheckStatus.PASS,
        severity=Severity.INFO,
        message=message,
        field_name=field_name,
    )


def fail_check(
    name: str,
    message: str,
    severity: Severity = Severity.ERROR,
    field_name: Optional[str] = None,
) -> CheckResult:
    return CheckResult(
        check_name=name,
        status=CheckStatus.FAIL,
        severity=severity,
        message=message,
        field_name=field_name,
    )
