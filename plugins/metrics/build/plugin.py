# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""Build metrics plugin for OSCAR."""

import os

from plugins.base_plugin import LambdaConfig, OscarPlugin, SecretConfig
from plugins.metrics.build.action_groups import get_action_groups
from plugins.metrics.build.instructions import AGENT_INSTRUCTION, COLLABORATOR_INSTRUCTION
from plugins.metrics.iam_policies import get_policies

# Keys to pass through from .env to Lambda (if set).
# config.py has its own defaults for each.
_METRICS_ENV_KEYS = [
    "OPENSEARCH_REGION", "OPENSEARCH_SERVICE",
    "OPENSEARCH_INTEGRATION_TEST_INDEX", "OPENSEARCH_BUILD_RESULTS_INDEX",
    "OPENSEARCH_RELEASE_METRICS_INDEX",
    "OPENSEARCH_LARGE_QUERY_SIZE", "OPENSEARCH_REQUEST_TIMEOUT",
    "BEDROCK_RESPONSE_MESSAGE_VERSION",
]


def _passthrough_env(keys):
    """Pass through env vars to Lambda — only if set."""
    return {k: os.environ[k] for k in keys if k in os.environ}


class MetricsBuildPlugin(OscarPlugin):

    @property
    def name(self):
        return "metrics-build"

    def get_lambda_config(self):
        return LambdaConfig(
            entry="plugins/metrics/lambda",
            timeout_seconds=180,
            memory_size=1024,
            reserved_concurrency=100,
            needs_vpc=True,
            environment_variables=_passthrough_env(_METRICS_ENV_KEYS),
        )

    def get_iam_policies(self, account_id, region, env):
        return get_policies(account_id, region, env)

    def get_action_groups(self, lambda_arn):
        return get_action_groups(lambda_arn)

    def get_agent_instruction(self):
        return AGENT_INSTRUCTION

    def get_collaborator_instruction(self):
        return COLLABORATOR_INSTRUCTION

    def get_collaborator_name(self):
        return "Build-Metrics-Specialist"

    def get_access_level(self):
        return "both"

    def get_secrets(self):
        return [
            SecretConfig(
                name_suffix="env",
                description="Metrics plugin secrets (cross-account role ARN, OpenSearch host, etc.)",
                env_var="METRICS_SECRET_NAME",
            ),
        ]

    def get_managed_policies(self):
        return [
            "service-role/AWSLambdaBasicExecutionRole",
            "service-role/AWSLambdaVPCAccessExecutionRole",
        ]
