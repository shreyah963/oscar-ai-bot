# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""Direct GitHub REST API client for operations not supported by the MCP server.

Used for: transfer_issue, add_comment, bulk_comment, community metrics.
"""

import json
import logging
import os
import re
from typing import Dict, List, Tuple

import boto3
import requests

from http_client import API_BASE, GitHubAPIError, get, post, put, request, ORG

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _get_issue_node_id(token: str, owner: str, repo: str, issue_number: int) -> str:
    """Get the GraphQL node ID for an issue."""
    result = get(token, f"/repos/{owner}/{repo}/issues/{issue_number}")
    node_id = result.get("node_id")
    if not node_id:
        raise GitHubAPIError(404, f"Issue #{issue_number} not found in {owner}/{repo}",
                             f"{API_BASE}/repos/{owner}/{repo}/issues/{issue_number}")
    return node_id


def _get_repo_node_id(token: str, owner: str, repo: str) -> str:
    """Get the GraphQL node ID for a repository."""
    result = get(token, f"/repos/{owner}/{repo}")
    node_id = result.get("node_id")
    if not node_id:
        raise GitHubAPIError(404, f"Repository {owner}/{repo} not found",
                             f"{API_BASE}/repos/{owner}/{repo}")
    return node_id


def transfer_issue(
    token: str, owner: str, repo: str, issue_number: int, target_repo: str,
) -> str:
    """Transfer an issue to another repository using the GraphQL API."""
    issue_node_id = _get_issue_node_id(token, owner, repo, issue_number)
    repo_node_id = _get_repo_node_id(token, owner, target_repo)

    query = """
    mutation($issueId: ID!, $repoId: ID!) {
      transferIssue(input: {issueId: $issueId, repositoryId: $repoId}) {
        issue {
          number
          url
          title
          repository {
            nameWithOwner
          }
        }
      }
    }
    """
    result = post(token, "/graphql", json_body={
        "query": query,
        "variables": {"issueId": issue_node_id, "repoId": repo_node_id},
    })

    errors = result.get("errors")
    if errors:
        msg = "; ".join(e.get("message", "") for e in errors)
        raise GitHubAPIError(422, f"GraphQL error: {msg}", f"{API_BASE}/graphql")

    issue_data = result.get("data", {}).get("transferIssue", {}).get("issue", {})
    return json.dumps({
        "status": "success",
        "new_issue_number": issue_data.get("number"),
        "new_url": issue_data.get("url"),
        "title": issue_data.get("title"),
        "new_repository": issue_data.get("repository", {}).get("nameWithOwner"),
    })


def add_comment(
    token: str, owner: str, repo: str, issue_number: int, body: str,
) -> str:
    """Add a comment to an issue or pull request."""
    result = post(token, f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
                  json_body={"body": body})
    return json.dumps(result)


def bulk_comment(
    token: str, owner: str, repo: str, issue_numbers: List[int], body: str,
) -> str:
    """Add the same comment to multiple issues/PRs. Returns per-issue results."""
    results = []
    for num in issue_numbers:
        try:
            resp = post(token, f"/repos/{owner}/{repo}/issues/{num}/comments",
                        json_body={"body": body})
            results.append({"issue_number": num, "status": "success", "url": resp.get("html_url", "")})
        except (GitHubAPIError, Exception) as e:
            results.append({"issue_number": num, "status": "error", "error": str(e)})
    return json.dumps({
        "results": results,
        "total": len(issue_numbers),
        "succeeded": sum(1 for r in results if r["status"] == "success"),
    })


def add_collaborator(
    token: str, owner: str, repo: str, username: str, permission: str = "maintain",
) -> str:
    """Add a user as a repository collaborator with the given permission level.

    Falls back to 'push' if 'maintain' is rejected (personal repos don't support maintain).
    """
    try:
        result = put(
            token,
            f"/repos/{owner}/{repo}/collaborators/{username}",
            json_body={"permission": permission},
        )
    except GitHubAPIError as e:
        if e.status_code == 422 and permission == "maintain":
            logger.warning(
                "maintain permission rejected for %s/%s, falling back to push",
                owner, repo,
            )
            permission = "push"
            result = put(
                token,
                f"/repos/{owner}/{repo}/collaborators/{username}",
                json_body={"permission": permission},
            )
        else:
            raise
    return json.dumps({
        "status": "success",
        "username": username,
        "repository": f"{owner}/{repo}",
        "permission": permission,
        "detail": result,
    })


# ---------------------------------------------------------------------------
# Community Metrics
# ---------------------------------------------------------------------------

_MAINTAINER_TITLE_RE = re.compile(
    r"\[GitHub Request\]\s+Add\s+(.+?)\s+to\s+(.+?)\s+maintainers",
    re.IGNORECASE,
)


def _get_user_company(token: str, username: str) -> str:
    """Fetch the company/affiliation field from a GitHub user profile."""
    try:
        data = get(token, f"/users/{username}")
        return data.get("company") or ""
    except Exception as e:
        logger.warning("Failed to fetch profile for %s: %s", username, e)
        return ""


def _date_filter(status: str, since: str, until: str) -> str:
    if status == "open":
        return f"created:{since}..{until}"
    return f"closed:{since}..{until}"


def get_new_maintainers(
    token: str, org: str, since: str, until: str, status: str = "closed",
) -> str:
    """Find maintainer requests via [GitHub Request] issues in org/.github."""
    status = status.lower() if status else "closed"
    query = (
        f"repo:{org}/.github "
        f"is:issue is:{status} "
        f"\"[GitHub Request] Add\" \"maintainers\" in:title "
        f"{_date_filter(status, since, until)}"
    )
    url = f"{API_BASE}/search/issues?q={requests.utils.quote(query)}&per_page=100&sort=updated&order=desc"
    result = request("GET", url, token)

    maintainers = []
    for item in result.get("items", []):
        title = item.get("title", "")
        match = _MAINTAINER_TITLE_RE.search(title)
        if not match:
            continue
        handle = match.group(1).strip().lstrip("@")
        repo = match.group(2).strip()
        affiliation = _get_user_company(token, handle)
        entry = {
            "github_handle": handle,
            "repository": repo,
            "affiliation": affiliation,
            "issue_url": item.get("html_url", ""),
        }
        if status == "open":
            entry["created_at"] = item.get("created_at", "")
        else:
            entry["closed_at"] = item.get("closed_at", "")
        maintainers.append(entry)

    return json.dumps({
        "maintainers": maintainers,
        "total": len(maintainers),
        "status": status,
        "period": f"{since} to {until}",
    })


_REPO_REQUEST_TITLE_RE = re.compile(
    r"\[Repository Request\]:?\s*(.+)",
    re.IGNORECASE,
)


def get_new_repositories(
    token: str, org: str, since: str, until: str, status: str = "closed",
) -> str:
    """Find repo requests via [Repository Request] issues in org/.github."""
    status = status.lower() if status else "closed"
    query = (
        f"repo:{org}/.github "
        f"is:issue is:{status} "
        f"\"[Repository Request]\" in:title "
        f"{_date_filter(status, since, until)}"
    )
    url = f"{API_BASE}/search/issues?q={requests.utils.quote(query)}&per_page=100&sort=updated&order=desc"
    result = request("GET", url, token)

    repos = []
    for item in result.get("items", []):
        title = item.get("title", "")
        match = _REPO_REQUEST_TITLE_RE.search(title)
        if not match:
            continue
        repo_name = match.group(1).strip()
        entry = {
            "name": repo_name,
            "issue_url": item.get("html_url", ""),
        }
        if status == "open":
            entry["created_at"] = item.get("created_at", "")
        else:
            entry["closed_at"] = item.get("closed_at", "")
        repos.append(entry)

    return json.dumps({
        "repositories": repos,
        "total": len(repos),
        "status": status,
        "period": f"{since} to {until}",
    })


def _is_org_member(token: str, org: str, username: str) -> bool:
    """Check if a user is a member of the given GitHub organization."""
    try:
        resp = requests.get(
            f"{API_BASE}/orgs/{org}/members/{username}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=30,
        )
        logger.info(
            "Org membership check: %s in %s → status=%d body=%s",
            username, org, resp.status_code, resp.text[:200],
        )
        return resp.status_code == 204
    except Exception as e:
        logger.warning("Failed to check org membership for %s: %s", username, e)
        return False


def _is_repo_maintainer(token: str, org: str, repo: str, username: str) -> bool:
    """Check if a user is listed in MAINTAINERS.md for the given repository."""
    try:
        resp = requests.get(
            f"{API_BASE}/repos/{org}/{repo}/contents/MAINTAINERS.md",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github.raw+json",
            },
            timeout=30,
        )
        if resp.status_code != 200:
            return False
        content = resp.text
        for line in content.splitlines():
            stripped = line.strip().lower()
            if username.lower() in stripped:
                return True
        return False
    except Exception as e:
        logger.warning("Failed to read MAINTAINERS.md for %s/%s: %s", org, repo, e)
        return False


_USER_PERMISSION_RE = re.compile(
    r"What is the type of request\?\s*\n+\s*User Permission",
    re.IGNORECASE,
)

_GITHUB_REQUEST_TITLE_RE = re.compile(r"^\[GitHub Request\]\s+", re.IGNORECASE)

_BODY_REPO_URL_RE = re.compile(
    r"https?://github\.com/([\w._-]+)/([\w._-]+)",
    re.IGNORECASE,
)
_BODY_AT_MENTION_RE = re.compile(r"@([\w-]+)")


def _parse_with_llm(title: str, body: str) -> Tuple[str, str, str]:
    """Use Bedrock Claude to extract nominee, repo owner, and repo name from unstructured text."""
    try:
        bedrock = boto3.client("bedrock-runtime")
        prompt = (
            "Extract exactly three pieces of information from this GitHub issue.\n"
            "1. The GitHub username of the person being nominated as maintainer.\n"
            "2. The owner/organization of the target repository.\n"
            "3. The repository name (just the repo name, not the full URL).\n\n"
            f"Issue title: {title}\n\n"
            f"Issue body:\n{body}\n\n"
            "Respond with ONLY a JSON object like: "
            '{\"nominee\": \"username\", \"repo_owner\": \"owner\", \"repo\": \"repo-name\"}\n'
            "If you cannot determine one of these, use an empty string for that field."
        )
        response = bedrock.invoke_model(
            modelId="anthropic.claude-haiku-4-5-20251001-v1:0",
            contentType="application/json",
            accept="application/json",
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": prompt}],
            }),
        )
        result = json.loads(response["body"].read())
        text = result.get("content", [{}])[0].get("text", "")
        parsed = json.loads(text)
        return (
            parsed.get("nominee", "").strip().lstrip("@"),
            parsed.get("repo_owner", "").strip(),
            parsed.get("repo", "").strip(),
        )
    except Exception as e:
        logger.warning("LLM parsing failed: %s", e)
        return ("", "", "")


def _parse_nominee_and_repo(title: str, body: str) -> Tuple[str, str, str]:
    """Extract nominee, repo owner, and repo name. Returns (nominee, repo_owner, repo_name)."""
    nominee = ""
    repo_owner = ""
    target_repo = ""

    title_match = _MAINTAINER_TITLE_RE.search(title)
    if title_match:
        nominee = title_match.group(1).strip().lstrip("@")
        target_repo = title_match.group(2).strip()

    if not nominee:
        mentions = _BODY_AT_MENTION_RE.findall(body)
        for m in mentions:
            if not m.endswith("[bot]") and m.lower() != "oscar-github-agent-test":
                nominee = m
                break

    url_match = _BODY_REPO_URL_RE.search(body)
    if url_match:
        repo_owner = url_match.group(1).strip()
        target_repo = url_match.group(2).strip()

    if nominee and target_repo:
        return nominee, repo_owner, target_repo

    llm_nominee, llm_owner, llm_repo = _parse_with_llm(title, body)
    if not nominee:
        nominee = llm_nominee
    if not repo_owner:
        repo_owner = llm_owner
    if not target_repo:
        target_repo = llm_repo

    return nominee, repo_owner, target_repo


def verify_maintainer_request(
    token: str, request_repo_owner: str, request_repo: str, issue_number: int,
) -> str:
    """Verify a maintainer request issue and post an approval comment if all checks pass.

    Validation steps:
    1. Title starts with '[GitHub Request]'
    2. Issue body contains 'User Permission' as the request type
    3. Issue has the 'github-request' label
    4. The nominee (parsed from body) is a member of the opensearch-project org
    5. The issue opener is already a maintainer of the target repo (parsed from body)
    """
    full_repo = f"{request_repo_owner}/{request_repo}"
    issue = get(token, f"/repos/{full_repo}/issues/{issue_number}")

    title = issue.get("title", "")
    body = issue.get("body", "") or ""
    labels = [l["name"].lower() for l in issue.get("labels", [])]
    opener = (issue.get("user") or {}).get("login", "")

    checks = {}

    # 1. Title must start with '[GitHub Request]'
    has_prefix = bool(_GITHUB_REQUEST_TITLE_RE.match(title))
    checks["title_format"] = {
        "passed": has_prefix,
        "detail": (
            f"Title has '[GitHub Request]' prefix: '{title}'"
            if has_prefix
            else f"Title missing '[GitHub Request]' prefix: '{title}'"
        ),
    }

    if not has_prefix:
        return json.dumps({
            "status": "error",
            "all_passed": False,
            "checks": checks,
            "message": f"Title missing '[GitHub Request]' prefix. Got: '{title}'",
        })

    # 2. Issue body contains 'User Permission' as request type
    body_match = _USER_PERMISSION_RE.search(body)
    checks["request_type"] = {
        "passed": body_match is not None,
        "detail": (
            "Issue body contains 'User Permission' request type"
            if body_match
            else "Issue body missing 'User Permission' under 'What is the type of request?'"
        ),
    }

    # 3. Label check
    has_label = "github-request" in labels
    checks["label"] = {
        "passed": has_label,
        "detail": (
            "'github-request' label present"
            if has_label
            else f"Missing 'github-request' label. Found: {labels}"
        ),
    }

    # Parse nominee, repo owner, and repo name from title/body, falling back to LLM
    nominee, repo_owner, target_repo = _parse_nominee_and_repo(title, body)
    # Separate env var for org membership verification
    maintainer_org = os.environ.get("MAINTAINER_ORG", ORG)
    repo_check_owner = repo_owner or maintainer_org

    if not nominee:
        checks["nominee_found"] = {
            "passed": False,
            "detail": "Could not parse nominee GitHub username from issue body",
        }
    if not target_repo:
        checks["repo_found"] = {
            "passed": False,
            "detail": "Could not parse target repository from issue body",
        }

    if not nominee or not target_repo:
        failed = [f"{n}: {c['detail']}" for n, c in checks.items() if not c["passed"]]
        return json.dumps({
            "status": "rejected",
            "all_passed": False,
            "checks": checks,
            "message": "Maintainer request verification failed:\n" + "\n".join(f"  ✗ {f}" for f in failed),
        })

    # 4. Nominee is a member of opensearch-project org
    is_member = _is_org_member(token, maintainer_org, nominee)
    checks["nominee_org_member"] = {
        "passed": is_member,
        "detail": (
            f"'{nominee}' is a member of {maintainer_org}"
            if is_member
            else f"'{nominee}' is NOT a member of {maintainer_org}"
        ),
    }

    # 5. Issue opener is already a maintainer of the target repo
    opener_is_maintainer = _is_repo_maintainer(token, repo_check_owner, target_repo, opener)
    checks["opener_is_maintainer"] = {
        "passed": opener_is_maintainer,
        "detail": (
            f"Issue opener '{opener}' is a maintainer of {repo_check_owner}/{target_repo}"
            if opener_is_maintainer
            else f"Issue opener '{opener}' is NOT listed in MAINTAINERS.md of {repo_check_owner}/{target_repo}"
        ),
    }

    all_passed = all(c["passed"] for c in checks.values())

    if not is_member and nominee:
        admin_tag_body = (
            f"Tagging @{maintainer_org}/admin to create a ticket with LF "
            f"to add **@{nominee}** to the **{maintainer_org}** organization."
        )
        post(token, f"/repos/{full_repo}/issues/{issue_number}/comments",
             json_body={"body": admin_tag_body})

    if all_passed:
        collab_result = None
        collab_error = None
        try:
            collab_result = add_collaborator(token, repo_check_owner, target_repo, nominee, "maintain")
        except Exception as e:
            collab_error = str(e)
            logger.error("Failed to add %s as collaborator to %s/%s: %s", nominee, repo_check_owner, target_repo, e)

        voting_note = (
            "This approval assumes that at least three positive (+1) maintainer votes "
            "have been obtained and no vetoes (-1) have been cast, as required for nomination."
        )
        next_steps = (
            f"Please add **@{nominee}** to the `MAINTAINERS.md` and `CODEOWNERS` files "
            f"in the **{repo_check_owner}/{target_repo}** repository."
        )

        if collab_error:
            approval_body = (
                f"**Maintainer Request Approved**\n\n"
                f"The nomination for **@{nominee}** was submitted by **@{opener}**, "
                f"who is a current maintainer of **{repo_check_owner}/{target_repo}**, "
                f"and **@{nominee}** is a verified member of the **{repo_check_owner}** organization. "
                f"This request is approved. {voting_note}\n\n"
                f"Tagging @{maintainer_org}/admin to add **@{nominee}** as a maintainer "
                f"to **{repo_check_owner}/{target_repo}**.\n\n"
                f"{next_steps}"
            )
        else:
            approval_body = (
                f"**Maintainer Request Approved & Completed**\n\n"
                f"The nomination for **@{nominee}** was submitted by **@{opener}**, "
                f"who is a current maintainer of **{repo_check_owner}/{target_repo}**, "
                f"and **@{nominee}** is a verified member of the **{repo_check_owner}** organization. "
                f"{voting_note}\n\n"
                f"**@{nominee}** has been added as a collaborator with **maintain** permission "
                f"to **{repo_check_owner}/{target_repo}**.\n\n"
                f"{next_steps}"
            )

        post(token, f"/repos/{full_repo}/issues/{issue_number}/comments",
             json_body={"body": approval_body})

    failed = [f"{n}: {c['detail']}" for n, c in checks.items() if not c["passed"]]
    result = {
        "status": "approved" if all_passed else "rejected",
        "all_passed": all_passed,
        "issue_number": issue_number,
        "issue_url": issue.get("html_url", ""),
        "nominee": nominee,
        "target_repo": target_repo,
        "opener": opener,
        "checks": checks,
    }
    if all_passed:
        result["collaborator_added"] = collab_error is None
        if collab_error:
            result["collaborator_error"] = collab_error
            result["message"] = (
                f"Maintainer request approved. Tagged @{maintainer_org}/admin "
                f"to add @{nominee} as a maintainer to {repo_check_owner}/{target_repo}."
            )
        else:
            result["message"] = (
                f"Maintainer request approved and @{nominee} added as maintainer "
                f"(maintain permission) to {repo_check_owner}/{target_repo}."
            )
    else:
        result["message"] = (
            f"Maintainer request verification failed:\n" + "\n".join(f"  ✗ {f}" for f in failed)
        )
    return json.dumps(result)


def get_external_contributors(
    token: str, org: str, repo: str, since: str, until: str,
) -> str:
    """Find unique PR authors for a repo in a date range and fetch their company affiliation."""
    query = f"repo:{org}/{repo} is:pr created:{since}..{until}"
    url = (
        f"{API_BASE}/search/issues"
        f"?q={requests.utils.quote(query)}"
        f"&per_page=100&sort=created&order=desc"
    )
    result = request("GET", url, token)

    seen: Dict[str, int] = {}
    for item in result.get("items", []):
        login = (item.get("user") or {}).get("login", "")
        if not login or login.endswith("[bot]"):
            continue
        seen[login] = seen.get(login, 0) + 1

    contributors = []
    for login, pr_count in seen.items():
        affiliation = _get_user_company(token, login)
        contributors.append({
            "github_handle": login,
            "affiliation": affiliation,
            "pr_count": pr_count,
        })

    return json.dumps({
        "contributors": contributors,
        "total": len(contributors),
        "repository": f"{org}/{repo}",
        "period": f"{since} to {until}",
    })
