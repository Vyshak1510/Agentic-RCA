from __future__ import annotations

from dataclasses import dataclass

from platform_core.models import RcaReport


@dataclass
class PublishResult:
    slack_message_id: str | None
    jira_issue_key: str | None


class Publisher:
    def publish(self, report: RcaReport, incident_key: str, enable_slack: bool = True, enable_jira: bool = True) -> PublishResult:
        # Stub implementation for OSS baseline. Integrations can replace this.
        slack_id = f"slack-{incident_key}" if enable_slack else None
        jira_key = f"RCA-{incident_key}" if enable_jira else None
        _ = report
        return PublishResult(slack_message_id=slack_id, jira_issue_key=jira_key)
