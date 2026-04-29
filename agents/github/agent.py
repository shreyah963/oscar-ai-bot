# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""GitHub agent for OSCAR."""

import os

from agents.base_agent import LambdaConfig, OscarAgent, SecretConfig
from agents.github.action_groups import get_action_groups
from agents.github.iam_policies import get_policies
from agents.github.instructions import AGENT_INSTRUCTION, COLLABORATOR_INSTRUCTION

GITHUB_ORG = os.environ.get("GITHUB_ORG", "opensearch-project")
MAINTAINER_ORG = os.environ.get("MAINTAINER_ORG", "opensearch-project")
MAINTAINER_REQUEST_REPO = os.environ.get("MAINTAINER_REQUEST_REPO", "opensearch-project/.github")

_ONBOARDING_ENV_KEYS = (
    "ADVISORIES_TARGET_REPO",
    "ADVISORIES_TARGET_OWNER",
    "ADVISORIES_BASE_BRANCH",
    "ADVISORIES_PROJECTS_PATH",
    "ADVISORIES_RELEASES_PATH",
    "WSS_TARGET_REPO",
    "WSS_TARGET_OWNER",
    "WSS_FILE_PATH",
    "AUTOMATION_APP_TARGET_REPO",
    "AUTOMATION_APP_TARGET_OWNER",
    "AUTOMATION_APP_FILE_PATH",
    "ONBOARDING_SSM_PREFIX",
)


class GitHubAgent(OscarAgent):

    @property
    def name(self):
        return "github"

    def get_lambda_config(self):
        return LambdaConfig(
            entry="agents/github/lambda",
            timeout_seconds=180,
            memory_size=1024,
            reserved_concurrency=10,
            environment_variables={
                "MCP_TOOLSETS": "issues,pull_requests",
                "MCP_READ_ONLY": "false",
                "GITHUB_ORG": GITHUB_ORG,
                "MAINTAINER_ORG": MAINTAINER_ORG,
                "MAINTAINER_REQUEST_REPO": MAINTAINER_REQUEST_REPO,
                **{k: os.environ[k] for k in (
                    "VERSION_INCREMENT_AUTHOR",
                    "RELEASE_NOTES_AUTHOR",
                    "CI_BOT_USERNAME",
                    "LLM_MODEL_ID",
                    *_ONBOARDING_ENV_KEYS,
                ) if k in os.environ},
            },
        )

    def get_iam_policies(self, account_id, region, env):
        return get_policies(account_id, region, env)

    def get_action_groups(self, lambda_arn):
        return get_action_groups(lambda_arn)

    def get_agent_instruction(self):
        parts = MAINTAINER_REQUEST_REPO.split("/", 1)
        owner = parts[0] if len(parts) == 2 else GITHUB_ORG
        repo_name = parts[1] if len(parts) == 2 else parts[0]
        return AGENT_INSTRUCTION.format(
            org=GITHUB_ORG,
            maintainer_request_repo=MAINTAINER_REQUEST_REPO,
            maintainer_request_repo_owner=owner,
            maintainer_request_repo_name=repo_name,
        )

    def get_collaborator_instruction(self):
        return COLLABORATOR_INSTRUCTION.format(org=GITHUB_ORG)

    def get_collaborator_name(self):
        return "GitHub-Specialist"

    def get_access_level(self):
        return "privileged"

    def get_secrets(self):
        return [
            SecretConfig(
                name_suffix="env",
                description="GitHub App credentials (App ID, private key, installation ID)",
                env_var="GITHUB_SECRET_NAME",
            ),
        ]

    def uses_knowledge_base(self):
        return False
