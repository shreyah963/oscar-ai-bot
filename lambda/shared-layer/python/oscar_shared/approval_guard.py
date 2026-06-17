# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""
Two-person approval guard.

Shared validation logic used by any Lambda that enforces ENABLE_2PR.
Approver must be a member of the GitHub admin team, resolved via DynamoDB identity mapping.
"""

import logging
import os
from typing import Any, Dict, Optional

import boto3
import requests

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

IDENTITY_TABLE_PREFIX = "oscar-identity"
ADMIN_TEAM_SLUG = os.environ.get("ADMIN_TEAM_SLUG", "admin")
ADMIN_TEAM_ORG = os.environ.get("ADMIN_TEAM_ORG", "oscar-test-org-shreyah963")

_dynamodb = None


def _get_dynamodb():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb")
    return _dynamodb


def _lookup_github_handle(slack_user_id: str) -> Optional[str]:
    """Look up a Slack user's GitHub handle from the identity table."""
    environment = os.environ.get("ENVIRONMENT", "dev")
    workspace_ids = os.environ.get("SLACK_WORKSPACE_IDS", "").split(",")

    dynamodb = _get_dynamodb()
    for workspace_id in workspace_ids:
        workspace_id = workspace_id.strip()
        if not workspace_id:
            continue
        table_name = f"{IDENTITY_TABLE_PREFIX}-{workspace_id}-{environment}"
        table = dynamodb.Table(table_name)
        resp = table.query(
            IndexName="slack-user-index",
            KeyConditionExpression="slack_user_id = :sid",
            ExpressionAttributeValues={":sid": slack_user_id},
        )
        items = resp.get("Items", [])
        for item in items:
            if item.get("status") == "active":
                return item.get("github_handle")
    return None


def _is_admin_team_member(github_token: str, github_handle: str) -> bool:
    """Check if a GitHub user is a member of the admin team."""
    url = f"https://api.github.com/orgs/{ADMIN_TEAM_ORG}/teams/{ADMIN_TEAM_SLUG}/members/{github_handle}"
    resp = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json",
        },
        timeout=10,
    )
    return resp.status_code == 204


def validate_two_person_approval(
    params: Dict[str, Any],
    enable_2pr: bool,
    action_label: str,
    github_token: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Validate two-person approval if the feature flag is enabled.

    Args:
        params: Request parameters dict (must contain requester_user_id, approver_user_id).
        enable_2pr: Whether the ENABLE_2PR flag is active.
        action_label: Human-readable label for logs (e.g. 'job=docker-scan', 'channel=C123').
        github_token: GitHub API token for team membership checks.

    Returns:
        None if validation passes (or flag is off). Otherwise a dict with
        'status'='error' and a 'message' suitable for returning to the caller.
    """
    if not enable_2pr:
        return None

    requester_user_id = params.get('requester_user_id')
    approver_user_id = params.get('approver_user_id')

    if not requester_user_id or not approver_user_id:
        return {
            'status': 'error',
            'message': 'SECURITY ERROR: requester_user_id and approver_user_id are required for two-person approval.',
        }

    if requester_user_id.strip() == approver_user_id.strip():
        return {
            'status': 'error',
            'message': (
                f'SECURITY ERROR: Self-approval is not permitted. The user who requested this action '
                f'({requester_user_id.strip()}) cannot also approve it. A different authorized user must confirm.'
            ),
        }

    # Admin team membership check (only when github_token is provided)
    approver_github = None
    if github_token:
        approver_github = _lookup_github_handle(approver_user_id.strip())
        if not approver_github:
            return {
                'status': 'error',
                'message': (
                    f'SECURITY ERROR: Approver ({approver_user_id.strip()}) has no linked GitHub account. '
                    f'They must run /oscar-link-github first.'
                ),
            }

        if not _is_admin_team_member(github_token, approver_github):
            return {
                'status': 'error',
                'message': (
                    f'SECURITY ERROR: Approver ({approver_github}) is not a member of the '
                    f'{ADMIN_TEAM_ORG}/{ADMIN_TEAM_SLUG} team. Only admin team members can approve.'
                ),
            }

    approver_label = approver_user_id.strip()
    if approver_github:
        approver_label = f'{approver_user_id.strip()} (github={approver_github})'

    logger.info(
        f'TWO_PERSON_APPROVAL: requester={requester_user_id.strip()}, '
        f'approver={approver_label}, {action_label}'
    )
    return None
