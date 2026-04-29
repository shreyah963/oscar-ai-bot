# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""Bedrock action group definitions for GitHub agent."""

from typing import List

from aws_cdk import aws_bedrock as bedrock


def _param(type_: str, description: str, required: bool = False):
    return bedrock.CfnAgent.ParameterDetailProperty(
        type=type_, description=description, required=required,
    )


def get_action_groups(lambda_arn: str) -> List[bedrock.CfnAgent.AgentActionGroupProperty]:
    executor = bedrock.CfnAgent.ActionGroupExecutorProperty(lambda_=lambda_arn)

    return [
        # -------------------------------------------------------- Group 1: Read Operations
        bedrock.CfnAgent.AgentActionGroupProperty(
            action_group_name="githubReadOperations",
            description="Read-only GitHub operations for PRs and issues",
            action_group_state="ENABLED",
            action_group_executor=executor,
            function_schema=bedrock.CfnAgent.FunctionSchemaProperty(
                functions=[
                    bedrock.CfnAgent.FunctionProperty(
                        name="get_pr_details",
                        description=(
                            "Get details of a pull request including title, state, author, "
                            "reviewers, merge status, and CI check results."
                        ),
                        parameters={
                            "repo": _param("string", "Repository name", True),
                            "pr_number": _param("string", "Pull request number", True),
                        },
                    ),
                    bedrock.CfnAgent.FunctionProperty(
                        name="list_prs",
                        description="List pull requests for a repository. Can filter by state.",
                        parameters={
                            "repo": _param("string", "Repository name", True),
                            "state": _param("string", "Filter by state: 'open', 'closed', or 'all'. Defaults to 'open'."),
                        },
                    ),
                    bedrock.CfnAgent.FunctionProperty(
                        name="get_issue_details",
                        description="Get details of an issue including title, state, assignees, labels, and comments.",
                        parameters={
                            "repo": _param("string", "Repository name", True),
                            "issue_number": _param("string", "Issue number", True),
                        },
                    ),
                    bedrock.CfnAgent.FunctionProperty(
                        name="list_issues",
                        description="List issues for a repository. Can filter by state and labels.",
                        parameters={
                            "repo": _param("string", "Repository name", True),
                            "state": _param("string", "Filter by state: 'open', 'closed', or 'all'. Defaults to 'open'."),
                            "labels": _param("string", "Comma-separated label names to filter by"),
                        },
                    ),
                    bedrock.CfnAgent.FunctionProperty(
                        name="search_issues",
                        description=(
                            "Search for issues using GitHub search syntax (already scoped to is:issue). "
                            "Examples: 'is:open label:bug', 'author:username is:closed'."
                        ),
                        parameters={
                            "query": _param("string", "Issue search query using GitHub search syntax", True),
                            "sort": _param("string", "Sort by: 'comments', 'reactions', 'created', 'updated'"),
                            "order": _param("string", "Sort order: 'asc' or 'desc'"),
                        },
                    ),
                    bedrock.CfnAgent.FunctionProperty(
                        name="search_pull_requests",
                        description=(
                            "Search for pull requests using GitHub search syntax (already scoped to is:pr). "
                            "Examples: 'is:open author:username', 'is:merged label:enhancement'."
                        ),
                        parameters={
                            "query": _param("string", "Pull request search query using GitHub search syntax", True),
                            "sort": _param("string", "Sort by: 'comments', 'reactions', 'created', 'updated'"),
                            "order": _param("string", "Sort order: 'asc' or 'desc'"),
                        },
                    ),
                ]
            ),
        ),

        # -------------------------------------------------------- Group 2: Write Operations
        bedrock.CfnAgent.AgentActionGroupProperty(
            action_group_name="githubWriteOperations",
            description="Write operations for PRs, issues, and comments (requires confirmation)",
            action_group_state="ENABLED",
            action_group_executor=executor,
            function_schema=bedrock.CfnAgent.FunctionSchemaProperty(
                functions=[
                    bedrock.CfnAgent.FunctionProperty(
                        name="merge_pr",
                        description=(
                            "Merge a pull request. "
                            "Requires explicit user confirmation before execution. "
                            "For automated PRs where only CI checks are failing, set force='true' "
                            "to override CI failures and merge anyway (only after the user "
                            "explicitly says 'force merge' or similar). "
                            "Non-CI guardrails (author, title, version, conflicts, draft) cannot be overridden."
                        ),
                        parameters={
                            "repo": _param("string", "Repository name", True),
                            "pr_number": _param("string", "Pull request number", True),
                            "merge_method": _param(
                                "string",
                                "Merge method: 'merge', 'squash', or 'rebase'. Defaults to 'merge'.",
                            ),
                            "commit_title": _param("string", "Custom merge commit title"),
                            "commit_message": _param("string", "Custom merge commit message"),
                            "force": _param(
                                "string",
                                "Set to 'true' to override CI check failures for automated PRs. "
                                "Only CI failures can be overridden; other guardrails cannot. "
                                "Only use when the user explicitly requests a force merge.",
                            ),
                        },
                    ),
                    bedrock.CfnAgent.FunctionProperty(
                        name="transfer_issue",
                        description=(
                            "Transfer an issue to another repository within the organization. "
                            "Target repository must be within the configured organization. "
                            "Requires explicit user confirmation before execution."
                        ),
                        parameters={
                            "repo": _param("string", "Source repository name", True),
                            "issue_number": _param("string", "Issue number to transfer", True),
                            "target_repo": _param(
                                "string",
                                "Target repository name to transfer the issue to",
                                True,
                            ),
                        },
                    ),
                    bedrock.CfnAgent.FunctionProperty(
                        name="create_issue",
                        description=(
                            "Create an issue on a repository. "
                            "Requires explicit user confirmation before execution."
                        ),
                        parameters={
                            "repo": _param("string", "Repository name", True),
                            "title": _param("string", "Issue title", True),
                            "body": _param("string", "Issue description body"),
                            "labels": _param("string", "Comma-separated label names to apply"),
                            "assignees": _param("string", "Comma-separated GitHub usernames to assign"),
                        },
                    ),
                    bedrock.CfnAgent.FunctionProperty(
                        name="close_issue",
                        description=(
                            "Close an issue with a reason. "
                            "Requires explicit user confirmation before execution."
                        ),
                        parameters={
                            "repo": _param("string", "Repository name", True),
                            "issue_number": _param("string", "Issue number to close", True),
                            "reason": _param(
                                "string",
                                "Reason for closing: 'completed' or 'not_planned'. Defaults to 'completed'.",
                            ),
                        },
                    ),
                    bedrock.CfnAgent.FunctionProperty(
                        name="add_comment",
                        description=(
                            "Add a comment to an issue or pull request. "
                            "Requires explicit user confirmation before execution."
                        ),
                        parameters={
                            "repo": _param("string", "Repository name", True),
                            "issue_number": _param("string", "Issue or pull request number", True),
                            "body": _param("string", "Comment body text (supports markdown)", True),
                        },
                    ),
                    bedrock.CfnAgent.FunctionProperty(
                        name="bulk_comment",
                        description=(
                            "Add the same comment to multiple issues or pull requests at once, "
                            "even across different repositories. "
                            "Useful for announcements, release notes, or campaign-style updates. "
                            "Requires explicit user confirmation before execution."
                        ),
                        parameters={
                            "issues": _param(
                                "string",
                                "Comma-separated list of issues in 'repo#number' format "
                                "(e.g. 'BigDataX#11,note-app#16'). Supports issues across different repos.",
                                True,
                            ),
                            "body": _param("string", "Comment body text (supports markdown)", True),
                        },
                    ),
                ]
            ),
        ),

        # ------------------------------------------------- Group 3: Bulk Merge Operations
        bedrock.CfnAgent.AgentActionGroupProperty(
            action_group_name="githubBulkMergeOperations",
            description="Bulk merge automated PRs (version increments and release notes) with guardrail validation",
            action_group_state="ENABLED",
            action_group_executor=executor,
            function_schema=bedrock.CfnAgent.FunctionSchemaProperty(
                functions=[
                    bedrock.CfnAgent.FunctionProperty(
                        name="list_merge_candidates",
                        description=(
                            "Search for automated PRs (version increments and release notes) "
                            "across all repositories in the organization for a given version. "
                            "Validates each PR against safety guardrails (author, title pattern, "
                            "version label, CI status, merge conflicts, draft state, version consistency) "
                            "and returns a detailed report. Always call this before bulk_merge_prs."
                        ),
                        parameters={
                            "version": _param("string", "Version to search for (e.g., '3.6.0')", True),
                            "organization": _param(
                                "string",
                                "GitHub organization to search (defaults to 'opensearch-project')",
                            ),
                        },
                    ),
                    bedrock.CfnAgent.FunctionProperty(
                        name="bulk_merge_prs",
                        description=(
                            "Merge all automated PRs that pass guardrail validation for the given version. "
                            "Re-validates every PR before merging. PRs that fail any guardrail are skipped. "
                            "CRITICAL: Only executes when confirmed=true. Use list_merge_candidates first "
                            "to review PRs, then call this only after user confirmation."
                        ),
                        parameters={
                            "version": _param("string", "Version to merge PRs for (e.g., '3.6.0')", True),
                            "organization": _param(
                                "string",
                                "GitHub organization (defaults to 'opensearch-project')",
                            ),
                            "confirmed": _param(
                                "string",
                                "REQUIRED: Must be 'true' to execute. Set to 'true' ONLY after user explicitly confirms.",
                                True,
                            ),
                            "force": _param(
                                "string",
                                "Set to 'true' to override CI check failures and force merge. "
                                "Only CI failures can be overridden; other guardrails (author, title, "
                                "version, conflicts, draft) cannot. Only use when the user explicitly "
                                "requests a force merge.",
                            ),
                        },
                    ),
                ]
            ),
        ),

        # -------------------------------------------- Group 4: Community Metrics
        bedrock.CfnAgent.AgentActionGroupProperty(
            action_group_name="githubCommunityMetrics",
            description="Read-only community metrics: new maintainers, new repositories, and external contributors",
            action_group_state="ENABLED",
            action_group_executor=executor,
            function_schema=bedrock.CfnAgent.FunctionSchemaProperty(
                functions=[
                    bedrock.CfnAgent.FunctionProperty(
                        name="get_new_maintainers",
                        description=(
                            "Find maintainer requests or additions during a date range by searching "
                            "'[GitHub Request] Add <user> to <repo> maintainers' issues in "
                            "the .github repo. Use status='open' for pending requests, "
                            "status='closed' for completed additions. Returns github handle, "
                            "repository, affiliation (company from their GitHub profile), and date."
                        ),
                        parameters={
                            "since": _param("string", "Start date in YYYY-MM-DD format", True),
                            "until": _param("string", "End date in YYYY-MM-DD format", True),
                            "status": _param(
                                "string",
                                "Issue state: 'open' for pending requests, 'closed' for completed additions. Defaults to 'closed'.",
                            ),
                            "organization": _param(
                                "string",
                                "GitHub organization (defaults to 'opensearch-project')",
                            ),
                        },
                    ),
                    bedrock.CfnAgent.FunctionProperty(
                        name="get_new_repositories",
                        description=(
                            "Find repository creation requests or repos added during a date range. "
                            "Searches '[Repository Request]' issues. Use status='open' for pending "
                            "requests, status='closed' for completed additions."
                        ),
                        parameters={
                            "since": _param("string", "Start date in YYYY-MM-DD format", True),
                            "until": _param("string", "End date in YYYY-MM-DD format", True),
                            "status": _param(
                                "string",
                                "Issue state: 'open' for pending requests, 'closed' for completed additions. Defaults to 'closed'.",
                            ),
                            "organization": _param(
                                "string",
                                "GitHub organization (defaults to 'opensearch-project')",
                            ),
                        },
                    ),
                    bedrock.CfnAgent.FunctionProperty(
                        name="get_repo_maintainers",
                        description=(
                            "Get the current maintainers of a repository by parsing its MAINTAINERS.md file. "
                            "Returns GitHub handles and display names. Use this when the user asks to "
                            "tag or mention maintainers (e.g. in bulk comments), or to look up who "
                            "maintains a specific repo."
                        ),
                        parameters={
                            "repo": _param("string", "Repository name", True),
                            "organization": _param(
                                "string",
                                "GitHub organization (defaults to 'opensearch-project')",
                            ),
                        },
                    ),
                    bedrock.CfnAgent.FunctionProperty(
                        name="get_external_contributors",
                        description=(
                            "Find unique PR authors for a repository in a date range and fetch "
                            "their company/affiliation from their GitHub profile. Useful for "
                            "identifying external (non-Amazon/AWS) contributors."
                        ),
                        parameters={
                            "repo": _param("string", "Repository name", True),
                            "since": _param("string", "Start date in YYYY-MM-DD format", True),
                            "until": _param("string", "End date in YYYY-MM-DD format", True),
                            "organization": _param(
                                "string",
                                "GitHub organization (defaults to 'opensearch-project')",
                            ),
                        },
                    ),
                ]
            ),
        ),

        # ---------------------------------------- Group 5: Maintainer Request Verification
        bedrock.CfnAgent.AgentActionGroupProperty(
            action_group_name="githubMaintainerVerification",
            description="Verify maintainer request issues and manage repository collaborators",
            action_group_state="ENABLED",
            action_group_executor=executor,
            function_schema=bedrock.CfnAgent.FunctionSchemaProperty(
                functions=[
                    bedrock.CfnAgent.FunctionProperty(
                        name="verify_maintainer_request",
                        description=(
                            "Verify a maintainer request issue. Checks that: "
                            "(1) title starts with '[GitHub Request]', "
                            "(2) issue body contains 'User Permission' as request type, "
                            "(3) issue has the 'github-request' label, "
                            "(4) the nominee is a member of the opensearch-project org, "
                            "(5) the issue opener is already a maintainer of the target repo "
                            "(listed in MAINTAINERS.md). If all checks pass, the nominee is "
                            "automatically added as a repository collaborator with maintain "
                            "permission and an approval comment is posted on the issue."
                        ),
                        parameters={
                            "request_repo_owner": _param(
                                "string",
                                "Owner of the repo where the request issue lives",
                                True,
                            ),
                            "request_repo": _param(
                                "string",
                                "Repository name where the request issue lives",
                                True,
                            ),
                            "issue_number": _param("string", "Issue number of the maintainer request", True),
                        },
                    ),
                    bedrock.CfnAgent.FunctionProperty(
                        name="add_collaborator",
                        description=(
                            "Add a user as a repository collaborator with a specified permission level. "
                            "Uses the GitHub Collaborators API (Repo Settings → Collaborators → Add people). "
                            "Requires explicit user confirmation before execution."
                        ),
                        parameters={
                            "repo": _param("string", "Repository name", True),
                            "username": _param("string", "GitHub username to add as collaborator", True),
                            "permission": _param(
                                "string",
                                "Permission level: 'pull', 'triage', 'push', 'maintain', or 'admin'. Defaults to 'maintain'.",
                            ),
                        },
                    ),
                ]
            ),
        ),

        # ---------------------------------------- Group 6: Repository Onboarding
        bedrock.CfnAgent.AgentActionGroupProperty(
            action_group_name="githubRepoOnboarding",
            description="Automate new repository onboarding: branch protection, secrets, collaborators, teams, labels, and cross-repo config PRs",
            action_group_state="ENABLED",
            action_group_executor=executor,
            function_schema=bedrock.CfnAgent.FunctionSchemaProperty(
                functions=[
                    bedrock.CfnAgent.FunctionProperty(
                        name="parse_repo_request",
                        description=(
                            "Parse a [Repository Request] issue to extract the repo name, "
                            "initial maintainers, and whether it is a bundle component. "
                            "Call this first when asked to onboard a repo from a request issue, "
                            "then use the extracted values to call onboard_repo."
                        ),
                        parameters={
                            "repo": _param("string", "Repository where the request issue lives", True),
                            "issue_number": _param("string", "Issue number of the repository request", True),
                        },
                    ),
                    bedrock.CfnAgent.FunctionProperty(
                        name="onboard_repo",
                        description=(
                            "Run all repository onboarding steps: branch protection, CI/CD configuration, "
                            "collaborators, teams, labels, and cross-repo config PRs. "
                            "CI/CD credentials are auto-fetched from SSM. "
                            "Returns a detailed step-by-step status report. "
                            "Requires explicit user confirmation before execution."
                        ),
                        parameters={
                            "repo": _param("string", "Repository name to onboard", True),
                            "maintainers": _param(
                                "string",
                                "Comma-separated GitHub usernames of initial maintainers (up to 3)",
                                True,
                            ),
                            "is_bundle_component": _param(
                                "string",
                                "Set to 'true' if this repo is a confirmed bundle component. Defaults to 'false'.",
                            ),
                            "admin_team": _param("string", "Admin team slug. Defaults to 'admin'."),
                            "triage_team": _param("string", "Triage team slug. Defaults to 'triage'."),
                        },
                    ),
                    bedrock.CfnAgent.FunctionProperty(
                        name="set_branch_protection",
                        description=(
                            "Apply standard branch protection rules to a repository. "
                            "Protects 'main' branch via Branch Protection API and 'backport*' branches "
                            "via Repository Rulesets API. Matches opensearch-core reference configuration."
                        ),
                        parameters={
                            "repo": _param("string", "Repository name", True),
                        },
                    ),
                    bedrock.CfnAgent.FunctionProperty(
                        name="add_repo_secret",
                        description=(
                            "Add or update a GitHub Actions CI/CD credential on a repository. "
                            "Encrypts the value and stores it via the GitHub API. "
                            "Requires explicit user confirmation before execution."
                        ),
                        parameters={
                            "repo": _param("string", "Repository name", True),
                            "secret_name": _param(
                                "string",
                                "Credential name to store",
                                True,
                            ),
                            "secret_value": _param("string", "Plain-text value to encrypt and store", True),
                        },
                    ),
                    bedrock.CfnAgent.FunctionProperty(
                        name="add_repo_collaborators",
                        description=(
                            "Add the CI bot and initial maintainers to a repository. "
                            "Adds opensearch-ci-bot with write access and up to 3 maintainers with maintain permission. "
                            "Requires explicit user confirmation before execution."
                        ),
                        parameters={
                            "repo": _param("string", "Repository name", True),
                            "maintainers": _param(
                                "string",
                                "Comma-separated GitHub usernames of initial maintainers (up to 3)",
                                True,
                            ),
                        },
                    ),
                    bedrock.CfnAgent.FunctionProperty(
                        name="add_repo_team",
                        description=(
                            "Add a GitHub team to a repository with a specified permission level. "
                            "Uses the Teams API to grant repository access. "
                            "Requires explicit user confirmation before execution."
                        ),
                        parameters={
                            "repo": _param("string", "Repository name", True),
                            "team_slug": _param("string", "Team slug (e.g., 'admin', 'triage')", True),
                            "permission": _param(
                                "string",
                                "Permission level: 'pull', 'triage', 'push', 'maintain', or 'admin'",
                                True,
                            ),
                        },
                    ),
                    bedrock.CfnAgent.FunctionProperty(
                        name="create_standard_labels",
                        description=(
                            "Create the standard set of project labels (Meta, RFC, Roadmap/*, skip-diff-*) "
                            "on a repository. If a label already exists, it is updated to match the standard. "
                            "Creates 14 labels total."
                        ),
                        parameters={
                            "repo": _param("string", "Repository name", True),
                        },
                    ),
                    bedrock.CfnAgent.FunctionProperty(
                        name="update_wss_scan_config",
                        description=(
                            "Create a PR on opensearch-build to add a new repository to the "
                            "WSS (Mend) vulnerability scan configuration file. "
                            "Requires explicit user confirmation before execution."
                        ),
                        parameters={
                            "repo": _param("string", "New repository name to add to scan config", True),
                        },
                    ),
                    bedrock.CfnAgent.FunctionProperty(
                        name="update_automation_app_config",
                        description=(
                            "Create a PR on automation-app to add a new repository to the "
                            "opensearch-project-resource.yml configuration. "
                            "Requires explicit user confirmation before execution."
                        ),
                        parameters={
                            "repo": _param("string", "New repository name to add to config", True),
                        },
                    ),
                    bedrock.CfnAgent.FunctionProperty(
                        name="onboard_to_advisories",
                        description=(
                            "Create a PR on security-advisories to add a new repository to "
                            "projects.json (always) and releases-origin-main.json (if bundle component). "
                            "Targets the 'next' branch. Requires explicit user confirmation."
                        ),
                        parameters={
                            "repo": _param("string", "New repository name", True),
                            "is_bundle_component": _param(
                                "string",
                                "Set to 'true' if this repo is a confirmed bundle component "
                                "(adds to releases-origin-main.json too). Defaults to 'false'.",
                            ),
                        },
                    ),
                ]
            ),
        ),
    ]
