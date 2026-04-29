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

from base64 import b64decode, b64encode

from http_client import API_BASE, GitHubAPIError, get, patch, post, put, request, ORG

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
    token: str, owner: str, issues: List[Tuple[str, int]], body: str,
) -> str:
    """Add the same comment to multiple issues/PRs, possibly across different repos.

    Each entry in *issues* is a (repo_name, issue_number) tuple.
    """
    results = []
    for repo, num in issues:
        try:
            resp = post(token, f"/repos/{owner}/{repo}/issues/{num}/comments",
                        json_body={"body": body})
            results.append({"repo": repo, "issue_number": num, "status": "success",
                            "url": resp.get("html_url", "")})
        except (GitHubAPIError, Exception) as e:
            results.append({"repo": repo, "issue_number": num, "status": "error",
                            "error": str(e)})
    return json.dumps({
        "results": results,
        "total": len(issues),
        "succeeded": sum(1 for r in results if r["status"] == "success"),
    })


def add_collaborator(
    token: str, owner: str, repo: str, username: str, permission: str = "maintain",
) -> str:
    """Add a user as a repository collaborator with the given permission level.

    Falls back to 'push' if 'maintain' is rejected (personal repos don't support maintain).
    Skips the repo owner since GitHub does not allow owners to be added as collaborators.
    """
    if username.lower() == owner.lower():
        return json.dumps({
            "status": "skipped",
            "username": username,
            "repository": f"{owner}/{repo}",
            "reason": "user is the repository owner",
        })
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
    r"\[GitHub Request\]\s+Add\s+(.+?)\s+(?:to\s+(.+?)\s+maintainer|as\s+.*?maintainer\s+to\s+(.+?)(?:\s+repo)?$)",
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
        f"\"[GitHub Request] Add\" \"maintainer\" in:title "
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
        repo = (match.group(2) or match.group(3) or "").strip()
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
    """Find repo requests via [Repository Request] issues."""
    requests_repo = os.environ.get("MAINTAINER_REQUEST_REPO", f"{org}/.github")
    status = status.lower() if status else "closed"
    query = (
        f"repo:{requests_repo} "
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


_MAINTAINERS_LINK_RE = re.compile(
    r"\[([^\]]+)\]\(https?://github\.com/([^)]+)\)",
)


def _parse_maintainers_fallback(content: str) -> list[dict]:
    """Regex fallback: extract GitHub handles from the Current Maintainers section."""
    in_current = False
    maintainers = []
    for line in content.splitlines():
        lower = line.strip().lower()
        if "current maintainer" in lower:
            in_current = True
            continue
        if in_current and ("emeritus" in lower or (lower.startswith("##") and "current" not in lower)):
            break
        if not in_current:
            continue
        match = _MAINTAINERS_LINK_RE.search(line)
        if match:
            maintainers.append({
                "github_id": match.group(2).strip().split("/")[-1],
                "name": match.group(1).strip(),
            })
    return maintainers


def get_repo_maintainers(token: str, owner: str, repo: str) -> str:
    """Fetch current maintainers from MAINTAINERS.md using LLM with regex fallback."""
    try:
        resp = requests.get(
            f"{API_BASE}/repos/{owner}/{repo}/contents/MAINTAINERS.md",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github.raw+json",
            },
            timeout=30,
        )
        if resp.status_code != 200:
            return json.dumps({
                "status": "error",
                "message": f"MAINTAINERS.md not found in {owner}/{repo} (HTTP {resp.status_code})",
            })

        content = resp.text
        maintainers = None

        try:
            prompt = (
                "Extract ONLY the current/active maintainers from this MAINTAINERS.md file. "
                "Do NOT include emeritus, former, or inactive maintainers.\n\n"
                f"{content}\n\n"
                "Respond with ONLY a JSON array of objects like:\n"
                '[{"github_id": "username", "name": "Display Name"}]\n'
                "If you cannot determine any maintainers, return an empty array []."
            )
            text = _invoke_llm(prompt, max_tokens=1000)
            maintainers = json.loads(text)
        except Exception as e:
            logger.warning("LLM maintainer parsing failed for %s/%s: %s, using regex fallback", owner, repo, e)

        if not maintainers:
            maintainers = _parse_maintainers_fallback(content)

        return json.dumps({
            "status": "success",
            "repo": f"{owner}/{repo}",
            "maintainers": maintainers,
            "total": len(maintainers),
        })
    except Exception as e:
        logger.warning("Failed to fetch maintainers for %s/%s: %s", owner, repo, e)
        return json.dumps({"status": "error", "message": str(e)})


_USER_PERMISSION_RE = re.compile(
    r"What is the type of request\?\s+User Permission",
    re.IGNORECASE,
)

_GITHUB_REQUEST_TITLE_RE = re.compile(r"^\[GitHub Request\]\s+", re.IGNORECASE)

_BODY_REPO_URL_RE = re.compile(
    r"https?://github\.com/([\w._-]+)/([\w._-]+)",
    re.IGNORECASE,
)
_BODY_AT_MENTION_RE = re.compile(r"@([\w-]+)")


LLM_MODEL_ID = os.environ.get("LLM_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")


def _invoke_llm(prompt: str, max_tokens: int = 256) -> str:
    """Invoke Bedrock Claude Haiku and return the text response."""
    bedrock = boto3.client("bedrock-runtime")
    response = bedrock.invoke_model(
        modelId=LLM_MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }),
    )
    result = json.loads(response["body"].read())
    return result.get("content", [{}])[0].get("text", "")


def _parse_with_llm(title: str, body: str) -> Tuple[str, str, str]:
    """Use Bedrock Claude to extract nominee, repo owner, and repo name from unstructured text."""
    try:
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
        text = _invoke_llm(prompt, max_tokens=100)
        parsed = json.loads(text)
        return (
            parsed.get("nominee", "").strip().lstrip("@"),
            parsed.get("repo_owner", "").strip(),
            parsed.get("repo", "").strip(),
        )
    except Exception as e:
        logger.warning("LLM parsing failed: %s", e)
        return ("", "", "")


_GITHUB_PROFILE_URL_RE = re.compile(
    r"https?://github\.com/([\w.-]+)(?:\s|$|[),\]])",
)


def _extract_maintainers_from_body(body: str) -> List[str]:
    """Best-effort regex extraction of maintainer GitHub usernames from issue body.

    Handles common formats:
      - https://github.com/username
      - @username
      - name - https://github.com/username
    """
    maintainers: List[str] = []
    seen: set = set()

    in_maintainer_section = False
    for line in body.splitlines():
        lower = line.strip().lower()
        if "initial maintainers" in lower or "maintainers list" in lower:
            in_maintainer_section = True
            continue
        if in_maintainer_section and lower and not lower.startswith(("-", "*", "•")) and "github.com" not in lower and "@" not in lower:
            in_maintainer_section = False

        if not in_maintainer_section:
            continue

        for m in _GITHUB_PROFILE_URL_RE.finditer(line):
            username = m.group(1).strip().rstrip("/")
            if username.lower() not in seen and not username.endswith("[bot]"):
                seen.add(username.lower())
                maintainers.append(username)

        for m in _BODY_AT_MENTION_RE.finditer(line):
            username = m.group(1)
            if username.lower() not in seen and not username.endswith("[bot]"):
                seen.add(username.lower())
                maintainers.append(username)

    return maintainers


def parse_repo_request(title: str, body: str) -> Dict:
    """Extract repo name, maintainers, and bundle status from a [Repository Request] issue."""
    try:
        prompt = (
            "Extract the following from this GitHub repository request issue.\n"
            "1. The repository name being requested (just the name, not a full URL).\n"
            "2. A list of GitHub usernames of the initial maintainers. "
            "Maintainers may be listed as profile URLs (e.g. https://github.com/username), "
            "@mentions, or 'name - URL' pairs. Extract ONLY the GitHub username from each.\n"
            "3. Whether this is a bundle component (true/false).\n\n"
            f"Issue title: {title}\n\n"
            f"Issue body:\n{body}\n\n"
            "Respond with ONLY a JSON object like:\n"
            '{\"repo\": \"repo-name\", \"maintainers\": [\"user1\", \"user2\"], '
            '\"is_bundle_component\": false}\n'
            "If you cannot determine a field, use an empty string for repo, "
            "an empty list for maintainers, and false for is_bundle_component."
        )
        text = _invoke_llm(prompt, max_tokens=200)
        parsed = json.loads(text)
        maintainers = parsed.get("maintainers", [])
        if isinstance(maintainers, str):
            maintainers = [m.strip().lstrip("@") for m in maintainers.split(",") if m.strip()]
        else:
            maintainers = [m.strip().lstrip("@") for m in maintainers if m.strip()]
    except Exception as e:
        logger.warning("LLM repo request parsing failed: %s", e)
        parsed = {}
        maintainers = []

    if not maintainers:
        maintainers = _extract_maintainers_from_body(body)

    repo_name = parsed.get("repo", "").strip() if parsed else ""
    if not repo_name:
        title_match = _REPO_REQUEST_TITLE_RE.search(title)
        if title_match:
            repo_name = title_match.group(1).strip()

    return {
        "repo": repo_name,
        "maintainers": maintainers,
        "is_bundle_component": bool(parsed.get("is_bundle_component", False)) if parsed else False,
    }


def _update_file_with_llm(current_content: str, new_repo: str, owner: str, file_description: str) -> str:
    """Use LLM to add a new entry to a config file matching the existing format."""
    prompt = (
        f"You are editing a configuration file: {file_description}\n\n"
        f"Current file content:\n```\n{current_content}\n```\n\n"
        f"TASK: Add the repository name '{new_repo}' to this file.\n\n"
        f"RULES:\n"
        f"1. Study the file format carefully before making changes.\n"
        f"2. If repos are in a comma-separated list (e.g. gitRepos=a,b,c), append ',{new_repo}' to the end of that list.\n"
        f"3. If repos are YAML list items (e.g. '- name: repo'), add a new '- name: {new_repo}' entry in alphabetical order.\n"
        f"4. If repos are JSON array entries, add a new entry matching the existing object structure.\n"
        f"5. If it is a plain text list with one repo per line, add '{new_repo}' on a new line.\n"
        f"6. Do NOT remove or modify any existing entries.\n"
        f"7. Do NOT add comments or explanations.\n"
        f"8. The output MUST differ from the input — you are adding '{new_repo}', which does not exist in the file yet.\n\n"
        f"Respond with ONLY the complete updated file content, nothing else. "
        f"No markdown code fences, no explanations."
    )
    max_tokens = max(len(current_content) * 2, 4096)
    return _invoke_llm(prompt, max_tokens=min(max_tokens, 16000))


def _parse_nominee_and_repo(title: str, body: str) -> Tuple[str, str, str]:
    """Extract nominee, repo owner, and repo name. Returns (nominee, repo_owner, repo_name)."""
    nominee = ""
    repo_owner = ""
    target_repo = ""

    title_match = _MAINTAINER_TITLE_RE.search(title)
    if title_match:
        target_repo = (title_match.group(2) or title_match.group(3) or "").strip()

    mentions = _BODY_AT_MENTION_RE.findall(body)
    for m in mentions:
        if not m.endswith("[bot]") and m.lower() != "oscar-github-agent-test":
            nominee = m
            break

    if not nominee and title_match:
        nominee = title_match.group(1).strip().lstrip("@")

    if not target_repo:
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


# ---------------------------------------------------------------------------
# Repository Onboarding
# ---------------------------------------------------------------------------

STANDARD_LABELS = [
    {"name": "Meta", "color": "5319e7", "description": "Meta issues serve as top level issues that group lower level changes into one bigger effort."},
    {"name": "RFC", "color": "04CB98", "description": ""},
    {"name": "Roadmap:Security", "color": "3B6BBD", "description": "Project-wide roadmap label"},
    {"name": "Roadmap:Security Analytics", "color": "3B6BBD", "description": "Project-wide roadmap label"},
    {"name": "Roadmap:Modular Architecture", "color": "3B6BBD", "description": "Project-wide roadmap label"},
    {"name": "Roadmap:Observability/Log Analytics", "color": "3B6BBD", "description": "Project-wide roadmap label"},
    {"name": "Roadmap:Ease of Use", "color": "3B6BBD", "description": "Project-wide roadmap label"},
    {"name": "Roadmap:Search and ML", "color": "3B6BBD", "description": "Project-wide roadmap label"},
    {"name": "Roadmap:Cost/Performance/Scale", "color": "3B6BBD", "description": "Project-wide roadmap label"},
    {"name": "Roadmap:Stability/Availability/Resiliency", "color": "3B6BBD", "description": "Project-wide roadmap label"},
    {"name": "Roadmap:Releases/Project Health", "color": "3B6BBD", "description": "Project-wide roadmap label"},
    {"name": "Roadmap:Vector Database/GenAI", "color": "3B6BBD", "description": "Project-wide roadmap label"},
    {"name": "skip-diff-analyzer", "color": "f3071c", "description": "Maintainer to skip code-diff-analyzer check, after reviewing issues in AI analysis."},
    {"name": "skip-diff-reviewer", "color": "2d5cd8", "description": "Maintainer to skip code-diff-reviewer check, after reviewing issues in AI analysis."},
]


def _create_ruleset(token: str, owner: str, repo: str, name: str, branch_pattern: str) -> Dict:
    """Create a repository ruleset, skipping if one with the same name already exists."""
    try:
        return post(
            token,
            f"/repos/{owner}/{repo}/rulesets",
            json_body={
                "name": name,
                "target": "branch",
                "enforcement": "active",
                "conditions": {
                    "ref_name": {"include": [branch_pattern], "exclude": []},
                },
                "rules": [
                    {
                        "type": "pull_request",
                        "parameters": {
                            "dismiss_stale_reviews_on_push": True,
                            "required_approving_review_count": 1,
                            "require_code_owner_review": False,
                            "require_last_push_approval": False,
                            "required_review_thread_resolution": False,
                        },
                    },
                    {"type": "non_fast_forward"},
                ],
            },
        )
    except GitHubAPIError as e:
        if e.status_code == 422 and "name must be unique" in str(e).lower():
            return {"status": "success", "detail": "ruleset already exists"}
        raise


def set_branch_protection(token: str, owner: str, repo: str) -> str:
    """Apply standard branch protection to main and backport* branches."""
    results = []

    # main branch — try Branch Protection API first, fall back to Rulesets
    try:
        put(
            token,
            f"/repos/{owner}/{repo}/branches/main/protection",
            json_body={
                "required_status_checks": None,
                "enforce_admins": None,
                "required_pull_request_reviews": {
                    "dismiss_stale_reviews": True,
                    "require_code_owner_reviews": False,
                    "required_approving_review_count": 1,
                },
                "restrictions": None,
                "allow_force_pushes": False,
                "allow_deletions": False,
            },
        )
        results.append({"branch": "main", "status": "success", "method": "branch_protection"})
    except GitHubAPIError as e:
        if e.status_code in (403, 404):
            try:
                _create_ruleset(token, owner, repo, "main-branch-protection", "refs/heads/main")
                results.append({"branch": "main", "status": "success", "method": "ruleset_fallback"})
            except GitHubAPIError as e2:
                results.append({"branch": "main", "status": "error", "error": str(e2)})
        else:
            results.append({"branch": "main", "status": "error", "error": str(e)})

    # backport* branches — Rulesets API (supports glob patterns)
    try:
        _create_ruleset(token, owner, repo, "backport-branch-protection", "refs/heads/backport*")
        results.append({"branch": "backport*", "status": "success", "method": "ruleset"})
    except GitHubAPIError as e:
        results.append({"branch": "backport*", "status": "error", "error": str(e)})

    return json.dumps({
        "status": "success" if all(r["status"] == "success" for r in results) else "partial",
        "results": results,
    })


def _encrypt_secret(public_key_b64: str, secret_value: str) -> str:
    """Encrypt a secret value using libsodium sealed box with the repo's public key."""
    from nacl.public import SealedBox, PublicKey
    public_key = PublicKey(b64decode(public_key_b64))
    sealed_box = SealedBox(public_key)
    encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
    return b64encode(encrypted).decode("utf-8")


def add_repo_secret(
    token: str, owner: str, repo: str, secret_name: str, secret_value: str,
) -> str:
    """Add or update a GitHub Actions repository secret."""
    pub_key_resp = get(token, f"/repos/{owner}/{repo}/actions/secrets/public-key")
    key_id = pub_key_resp["key_id"]
    public_key_b64 = pub_key_resp["key"]

    encrypted_value = _encrypt_secret(public_key_b64, secret_value)

    put(
        token,
        f"/repos/{owner}/{repo}/actions/secrets/{secret_name}",
        json_body={"encrypted_value": encrypted_value, "key_id": key_id},
    )
    return json.dumps({
        "status": "success",
        "credential": secret_name,
        "repository": f"{owner}/{repo}",
    })


CI_BOT_USERNAME = os.environ.get("CI_BOT_USERNAME", "opensearch-ci-bot")


def add_repo_collaborators(
    token: str, owner: str, repo: str, maintainers: List[str],
) -> str:
    """Add CI bot (push) and initial maintainers (maintain) to a repository."""
    results = []

    def _add(username, permission):
        try:
            resp = json.loads(add_collaborator(token, owner, repo, username, permission))
            results.append({"username": username, "permission": permission, "status": resp["status"],
                            **({"reason": resp["reason"]} if "reason" in resp else {})})
        except Exception as e:
            results.append({"username": username, "permission": permission, "status": "error", "error": str(e)})

    # CI bot with push (write) access
    _add(CI_BOT_USERNAME, "push")

    # Initial maintainers with maintain permission
    for username in maintainers[:3]:
        username = username.strip().lstrip("@")
        if not username:
            continue
        _add(username, "maintain")

    return json.dumps({
        "status": "success" if all(r["status"] in ("success", "skipped") for r in results) else "partial",
        "results": results,
        "total": len(results),
        "succeeded": sum(1 for r in results if r["status"] == "success"),
    })


def add_repo_team(
    token: str, org: str, repo: str, team_slug: str, permission: str,
) -> str:
    """Add a GitHub team to a repository with the specified permission level."""
    put(
        token,
        f"/orgs/{org}/teams/{team_slug}/repos/{org}/{repo}",
        json_body={"permission": permission},
    )
    return json.dumps({
        "status": "success",
        "team": team_slug,
        "repository": f"{org}/{repo}",
        "permission": permission,
    })


def create_standard_labels(token: str, owner: str, repo: str) -> str:
    """Create the standard set of project labels on a repository."""
    results = []
    for label in STANDARD_LABELS:
        body = {"name": label["name"], "color": label["color"]}
        if label["description"]:
            body["description"] = label["description"]
        try:
            post(token, f"/repos/{owner}/{repo}/labels", json_body=body)
            results.append({"name": label["name"], "status": "created"})
        except GitHubAPIError as e:
            if e.status_code == 422:
                try:
                    patch(
                        token,
                        f"/repos/{owner}/{repo}/labels/{requests.utils.quote(label['name'], safe='')}",
                        json_body=body,
                    )
                    results.append({"name": label["name"], "status": "updated"})
                except Exception as e2:
                    results.append({"name": label["name"], "status": "error", "error": str(e2)})
            else:
                results.append({"name": label["name"], "status": "error", "error": str(e)})

    return json.dumps({
        "status": "success" if all(r["status"] != "error" for r in results) else "partial",
        "results": results,
        "total": len(STANDARD_LABELS),
        "created": sum(1 for r in results if r["status"] == "created"),
        "updated": sum(1 for r in results if r["status"] == "updated"),
        "errors": sum(1 for r in results if r["status"] == "error"),
    })


def _create_pr_with_file_update(
    token: str,
    owner: str,
    target_repo: str,
    file_path: str,
    branch_name: str,
    base_branch: str,
    commit_message: str,
    pr_title: str,
    pr_body: str,
    update_fn,
) -> Dict:
    """Helper: create a branch, update a file, and open a PR.

    update_fn(current_content: str) -> str returns the new file content.
    """
    # Get base branch HEAD SHA
    ref_data = get(token, f"/repos/{owner}/{target_repo}/git/refs/heads/{base_branch}")
    base_sha = ref_data["object"]["sha"]

    # Create branch (or reset it if it already exists from a previous attempt)
    try:
        post(
            token,
            f"/repos/{owner}/{target_repo}/git/refs",
            json_body={"ref": f"refs/heads/{branch_name}", "sha": base_sha},
        )
    except GitHubAPIError as e:
        if e.status_code == 422:
            patch(
                token,
                f"/repos/{owner}/{target_repo}/git/refs/heads/{branch_name}",
                json_body={"sha": base_sha, "force": True},
            )
        else:
            raise

    # Read current file
    file_data = get(token, f"/repos/{owner}/{target_repo}/contents/{file_path}?ref={branch_name}")
    current_content = b64decode(file_data["content"]).decode("utf-8")
    file_sha = file_data["sha"]

    # Apply update
    new_content = update_fn(current_content)

    if new_content.strip() == current_content.strip():
        raise ValueError(f"LLM returned unchanged content for {file_path} — entry may already exist or update failed")

    # Commit updated file
    put(
        token,
        f"/repos/{owner}/{target_repo}/contents/{file_path}",
        json_body={
            "message": commit_message,
            "content": b64encode(new_content.encode("utf-8")).decode("utf-8"),
            "sha": file_sha,
            "branch": branch_name,
        },
    )

    # Create PR (or find existing one for this branch)
    try:
        pr = post(
            token,
            f"/repos/{owner}/{target_repo}/pulls",
            json_body={
                "title": pr_title,
                "body": pr_body,
                "head": branch_name,
                "base": base_branch,
            },
        )
    except GitHubAPIError as e:
        if e.status_code == 422:
            existing = get(token, f"/repos/{owner}/{target_repo}/pulls?head={owner}:{branch_name}&state=open")
            if existing:
                pr = existing[0]
            else:
                raise
        else:
            raise

    return {"pr_url": pr.get("html_url", ""), "pr_number": pr.get("number")}


ONBOARDING_SSM_PREFIX = os.environ.get("ONBOARDING_SSM_PREFIX", "/oscar/github/onboarding")

_ONBOARDING_SECRET_NAMES = ("BACKPORT_TOKEN", "CODECOV_TOKEN", "OP_SERVICE_ACCOUNT_TOKEN")


def _get_onboarding_secrets() -> Dict[str, str]:
    """Fetch onboarding secrets from SSM Parameter Store.

    Returns a dict mapping secret name to value for any parameters that exist
    under the configured prefix.
    """
    ssm = boto3.client("ssm")
    secrets: Dict[str, str] = {}
    for name in _ONBOARDING_SECRET_NAMES:
        param_name = f"{ONBOARDING_SSM_PREFIX}/{name}"
        try:
            resp = ssm.get_parameter(Name=param_name, WithDecryption=True)
            secrets[name] = resp["Parameter"]["Value"]
            logger.info("Loaded onboarding secret %s from SSM", name)
        except ssm.exceptions.ParameterNotFound:
            logger.info("SSM parameter %s not found, skipping", param_name)
        except Exception as e:
            logger.error("Failed to fetch SSM parameter %s: %s", param_name, e)
    if not secrets:
        logger.warning("No onboarding secrets found in SSM under %s", ONBOARDING_SSM_PREFIX)
    return secrets


WSS_TARGET_REPO = os.environ.get("WSS_TARGET_REPO", "opensearch-build")
WSS_TARGET_OWNER = os.environ.get("WSS_TARGET_OWNER", "")
WSS_FILE_PATH = os.environ.get("WSS_FILE_PATH", "tools/vulnerability-scan/wss-scan.config")

AUTOMATION_APP_TARGET_REPO = os.environ.get("AUTOMATION_APP_TARGET_REPO", "automation-app")
AUTOMATION_APP_TARGET_OWNER = os.environ.get("AUTOMATION_APP_TARGET_OWNER", "")
AUTOMATION_APP_FILE_PATH = os.environ.get("AUTOMATION_APP_FILE_PATH", "configs/resources/opensearch-project-resource.yml")


def update_wss_scan_config(token: str, owner: str, new_repo: str) -> str:
    """Create a PR to add a repo to wss-scan.config."""
    target_owner = WSS_TARGET_OWNER or owner

    def _append_repo(content: str) -> str:
        if new_repo in content:
            raise ValueError(f"{new_repo} already exists in wss-scan.config")
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("gitRepos="):
                lines[i] = line.rstrip() + f",{new_repo}"
                return "\n".join(lines) + ("\n" if content.endswith("\n") else "")
        raise ValueError("Could not find gitRepos= line in wss-scan.config")

    result = _create_pr_with_file_update(
        token=token,
        owner=target_owner,
        target_repo=WSS_TARGET_REPO,
        file_path=WSS_FILE_PATH,
        branch_name=f"oscar/onboard-{new_repo}",
        base_branch="main",
        commit_message=f"Add {new_repo} to WSS scan config",
        pr_title=f"[OSCAR] Onboard {new_repo} - add to WSS scan config",
        pr_body=(
            f"Add `{new_repo}` to the WSS (Mend) vulnerability scan configuration "
            f"as part of repository onboarding.\n\n"
            f"Created by OSCAR repo onboarding automation."
        ),
        update_fn=_append_repo,
    )
    return json.dumps({"status": "success", **result})


def update_automation_app_config(token: str, owner: str, new_repo: str) -> str:
    """Create a PR to add a repo to opensearch-project-resource.yml."""
    target_owner = AUTOMATION_APP_TARGET_OWNER or owner

    def _append_repo(content: str) -> str:
        if new_repo in content:
            raise ValueError(f"{new_repo} already exists in opensearch-project-resource.yml")
        lines = content.splitlines()
        last_repo_idx = None
        for i, line in enumerate(lines):
            if re.match(r"^\s+- name: ", line):
                last_repo_idx = i
        if last_repo_idx is None:
            raise ValueError("Could not find repository entries in opensearch-project-resource.yml")
        indent = re.match(r"^(\s+)", lines[last_repo_idx]).group(1)
        lines.insert(last_repo_idx + 1, f"{indent}- name: {new_repo}")
        return "\n".join(lines) + ("\n" if content.endswith("\n") else "")

    result = _create_pr_with_file_update(
        token=token,
        owner=target_owner,
        target_repo=AUTOMATION_APP_TARGET_REPO,
        file_path=AUTOMATION_APP_FILE_PATH,
        branch_name=f"oscar/onboard-{new_repo}",
        base_branch="main",
        commit_message=f"Add {new_repo} to opensearch-project-resource.yml",
        pr_title=f"[OSCAR] Onboard {new_repo} - add to automation-app config",
        pr_body=(
            f"Add `{new_repo}` to the automation-app resource configuration "
            f"as part of repository onboarding.\n\n"
            f"Created by OSCAR repo onboarding automation."
        ),
        update_fn=_append_repo,
    )
    return json.dumps({"status": "success", **result})


ADVISORIES_TARGET_REPO = os.environ.get("ADVISORIES_TARGET_REPO", "security-advisories")
ADVISORIES_TARGET_OWNER = os.environ.get("ADVISORIES_TARGET_OWNER", "")
ADVISORIES_BASE_BRANCH = os.environ.get("ADVISORIES_BASE_BRANCH", "next")
ADVISORIES_PROJECTS_PATH = os.environ.get("ADVISORIES_PROJECTS_PATH", "config/projects.json")
ADVISORIES_RELEASES_PATH = os.environ.get("ADVISORIES_RELEASES_PATH", "config/releases-origin-main.json")


def onboard_to_advisories(
    token: str, owner: str, new_repo: str, is_bundle_component: bool = False,
) -> str:
    """Create a PR on the advisories repo to add a repo to projects.json and optionally releases-origin-main.json."""
    branch_name = f"oscar/onboard-{new_repo}"
    base_branch = ADVISORIES_BASE_BRANCH
    target_repo = ADVISORIES_TARGET_REPO
    target_owner = ADVISORIES_TARGET_OWNER or owner
    projects_path = ADVISORIES_PROJECTS_PATH
    releases_path = ADVISORIES_RELEASES_PATH

    # Get base branch HEAD SHA
    ref_data = get(token, f"/repos/{target_owner}/{target_repo}/git/refs/heads/{base_branch}")
    base_sha = ref_data["object"]["sha"]

    # Create branch (or reset it if it already exists from a previous attempt)
    try:
        post(
            token,
            f"/repos/{target_owner}/{target_repo}/git/refs",
            json_body={"ref": f"refs/heads/{branch_name}", "sha": base_sha},
        )
    except GitHubAPIError as e:
        if e.status_code == 422:
            patch(
                token,
                f"/repos/{target_owner}/{target_repo}/git/refs/heads/{branch_name}",
                json_body={"sha": base_sha, "force": True},
            )
        else:
            raise

    files_updated = []

    # Always update projects.json
    file_data = get(token, f"/repos/{target_owner}/{target_repo}/contents/{projects_path}?ref={branch_name}")
    projects_content = b64decode(file_data["content"]).decode("utf-8")
    projects_sha = file_data["sha"]

    if new_repo not in projects_content:
        projects_data = json.loads(projects_content)
        projects_data.append({
            "name": new_repo,
            "repo": f"https://github.com/{owner}/{new_repo}.git",
        })
        updated_content = json.dumps(projects_data, indent=2) + "\n"
        put(
            token,
            f"/repos/{target_owner}/{target_repo}/contents/{projects_path}",
            json_body={
                "message": f"Add {new_repo} to projects.json",
                "content": b64encode(updated_content.encode("utf-8")).decode("utf-8"),
                "sha": projects_sha,
                "branch": branch_name,
            },
        )
        files_updated.append(projects_path)

    # Optionally update releases-origin-main.json
    if is_bundle_component:
        file_data = get(
            token,
            f"/repos/{target_owner}/{target_repo}/contents/{releases_path}?ref={branch_name}",
        )
        releases_content = b64decode(file_data["content"]).decode("utf-8")
        releases_sha = file_data["sha"]

        if new_repo not in releases_content:
            releases_data = json.loads(releases_content)
            releases_data.append({
                "name": new_repo,
                "repo": f"https://github.com/{owner}/{new_repo}.git",
            })
            updated_content = json.dumps(releases_data, indent=2) + "\n"
            put(
                token,
                f"/repos/{target_owner}/{target_repo}/contents/{releases_path}",
                json_body={
                    "message": f"Add {new_repo} to releases-origin-main.json",
                    "content": b64encode(updated_content.encode("utf-8")).decode("utf-8"),
                    "sha": releases_sha,
                    "branch": branch_name,
                },
            )
            files_updated.append(releases_path)

    # Create PR
    body_lines = [
        f"Add `{new_repo}` to advisories configuration as part of repository onboarding.\n",
        "**Files updated:**",
    ]
    for f in files_updated:
        body_lines.append(f"- `{f}`")
    if not files_updated:
        body_lines.append("- No changes needed (entries already exist)")
    body_lines.append("\nCreated by OSCAR repo onboarding automation.")

    pr = post(
        token,
        f"/repos/{target_owner}/{target_repo}/pulls",
        json_body={
            "title": f"[OSCAR] Onboard {new_repo} - add to advisories config",
            "body": "\n".join(body_lines),
            "head": branch_name,
            "base": base_branch,
        },
    )

    return json.dumps({
        "status": "success",
        "pr_url": pr.get("html_url", ""),
        "pr_number": pr.get("number"),
        "files_updated": files_updated,
        "is_bundle_component": is_bundle_component,
    })


def onboard_repo(
    token: str,
    owner: str,
    repo: str,
    maintainers: List[str],
    backport_token: str = "",
    codecov_token: str = "",
    op_service_account_token: str = "",
    is_bundle_component: bool = False,
    admin_team: str = "admin",
    triage_team: str = "triage",
) -> str:
    """Run all repository onboarding steps and return a step-by-step report."""
    steps = []
    prs_created = []

    def _run_step(name: str, fn):
        try:
            result = fn()
            parsed = json.loads(result) if isinstance(result, str) else result
            step = {"step": name, "status": parsed.get("status", "success"), "detail": parsed}
            if "pr_url" in parsed:
                prs_created.append({"step": name, "pr_url": parsed["pr_url"]})
            steps.append(step)
        except Exception as e:
            steps.append({"step": name, "status": "error", "detail": str(e)})

    # 1. Branch protection
    _run_step("branch_protection", lambda: set_branch_protection(token, owner, repo))

    # 2. Secrets — use explicit values first, fall back to SSM Parameter Store
    provided = {
        "BACKPORT_TOKEN": backport_token,
        "CODECOV_TOKEN": codecov_token,
        "OP_SERVICE_ACCOUNT_TOKEN": op_service_account_token,
    }
    secrets_to_add = [(k, v) for k, v in provided.items() if v]

    if not secrets_to_add:
        ssm_secrets = _get_onboarding_secrets()
        secrets_to_add = list(ssm_secrets.items())

    if secrets_to_add:
        def _add_ci_config():
            results = []
            for name, value in secrets_to_add:
                try:
                    add_repo_secret(token, owner, repo, name, value)
                    results.append({"credential": name, "status": "success"})
                except Exception as e:
                    results.append({"credential": name, "status": "error", "error": str(e)})
            return json.dumps({
                "status": "success" if all(r["status"] == "success" for r in results) else "partial",
                "configured": len([r for r in results if r["status"] == "success"]),
                "results": results,
            })
        _run_step("ci_cd_pipeline_config", _add_ci_config)
    else:
        steps.append({"step": "ci_cd_pipeline_config", "status": "skipped", "detail": "No CI/CD pipeline values found"})

    # 3. Collaborators
    _run_step("collaborators", lambda: add_repo_collaborators(token, owner, repo, maintainers))

    # 4. Teams
    def _add_teams():
        results = []
        for slug, perm in [(admin_team, "admin"), (triage_team, "triage")]:
            try:
                add_repo_team(token, owner, repo, slug, perm)
                results.append({"team": slug, "permission": perm, "status": "success"})
            except Exception as e:
                results.append({"team": slug, "permission": perm, "status": "error", "error": str(e)})
        return json.dumps({
            "status": "success" if all(r["status"] == "success" for r in results) else "partial",
            "results": results,
        })
    _run_step("teams", _add_teams)

    # 5. Labels
    _run_step("labels", lambda: create_standard_labels(token, owner, repo))

    # 6. WSS scan config PR
    _run_step("wss_scan_config", lambda: update_wss_scan_config(token, owner, repo))

    # 7. Automation app config PR
    _run_step("automation_app_config", lambda: update_automation_app_config(token, owner, repo))

    # 8. Security advisories PR
    _run_step("advisories", lambda: onboard_to_advisories(token, owner, repo, is_bundle_component))

    has_errors = any(s["status"] == "error" for s in steps)
    has_partial = any(s["status"] == "partial" for s in steps)

    return json.dumps({
        "repo": f"{owner}/{repo}",
        "status": "completed_with_errors" if (has_errors or has_partial) else "completed",
        "steps": steps,
        "prs_created": prs_created,
        "summary": {
            "total_steps": len(steps),
            "succeeded": sum(1 for s in steps if s["status"] == "success"),
            "partial": sum(1 for s in steps if s["status"] == "partial"),
            "errors": sum(1 for s in steps if s["status"] == "error"),
            "skipped": sum(1 for s in steps if s["status"] == "skipped"),
        },
    })
