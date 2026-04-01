# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
"""Integration tests validating plugins conform to the OscarPlugin interface
and are wired correctly into CDK stacks."""

import ast
import glob
import os

import pytest
from aws_cdk import App, Environment
from aws_cdk.assertions import Template

from plugins.base_plugin import LambdaConfig, OscarPlugin
from plugins.jenkins import JenkinsPlugin
from plugins.metrics import MetricsPlugin
from stacks.lambda_stack import OscarLambdaStack
from stacks.permissions_stack import OscarPermissionsStack
from stacks.secrets_stack import OscarSecretsStack
from stacks.storage_stack import OscarStorageStack
from stacks.vpc_stack import OscarVpcStack

ALL_PLUGINS = [JenkinsPlugin(), MetricsPlugin()]
PLUGIN_IDS = [p.name for p in ALL_PLUGINS]
ENV = Environment(account="123456789012", region="us-east-1")


# ---------------------------------------------------------------------------
# Plugin contract tests (parametrized over every plugin)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("plugin", ALL_PLUGINS, ids=PLUGIN_IDS)
class TestPluginContract:
    """Every plugin must satisfy the OscarPlugin interface."""

    def test_is_oscar_plugin_subclass(self, plugin):
        assert isinstance(plugin, OscarPlugin)

    def test_name_is_non_empty_string(self, plugin):
        assert isinstance(plugin.name, str)
        assert len(plugin.name) > 0

    def test_get_lambda_config_returns_lambda_config(self, plugin):
        config = plugin.get_lambda_config()
        assert isinstance(config, LambdaConfig)

    def test_lambda_entry_path_exists(self, plugin):
        config = plugin.get_lambda_config()
        assert os.path.isdir(config.entry), f"Entry path {config.entry} does not exist"

    def test_lambda_index_file_exists(self, plugin):
        config = plugin.get_lambda_config()
        index_path = os.path.join(config.entry, config.index)
        assert os.path.isfile(index_path), f"Index file {index_path} does not exist"

    def test_get_iam_policies_returns_list(self, plugin):
        policies = plugin.get_iam_policies("123456789012", "us-east-1", "dev")
        assert isinstance(policies, list)

    def test_get_action_groups_returns_list(self, plugin):
        # Action groups need a Lambda ARN; use a placeholder
        groups = plugin.get_action_groups("arn:aws:lambda:us-east-1:123456789012:function:placeholder")
        assert isinstance(groups, list)
        assert len(groups) >= 1, "Plugin must define at least one action group"

    def test_get_agent_instruction_non_empty(self, plugin):
        instruction = plugin.get_agent_instruction()
        assert isinstance(instruction, str)
        assert len(instruction) > 0

    def test_get_collaborator_instruction_non_empty(self, plugin):
        instruction = plugin.get_collaborator_instruction()
        assert isinstance(instruction, str)
        assert len(instruction) > 0

    def test_get_collaborator_name_non_empty(self, plugin):
        name = plugin.get_collaborator_name()
        assert isinstance(name, str)
        assert len(name) > 0

    def test_access_level_valid(self, plugin):
        level = plugin.get_access_level()
        assert level in ("privileged", "limited", "both"), \
            f"Invalid access level: {level}"


# ---------------------------------------------------------------------------
# Plugin registration tests
# ---------------------------------------------------------------------------

class TestPluginRegistration:
    """Validate the specific plugin set and their access levels."""

    def test_two_plugins_registered(self):
        assert len(ALL_PLUGINS) == 2

    def test_plugin_names_are_unique(self):
        names = [p.name for p in ALL_PLUGINS]
        assert len(names) == len(set(names)), f"Duplicate plugin names: {names}"

    def test_jenkins_is_privileged_only(self):
        assert JenkinsPlugin().get_access_level() == "privileged"

    def test_metrics_access_level(self):
        assert MetricsPlugin().get_access_level() == "both"


# ---------------------------------------------------------------------------
# CDK stack wiring tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def stacks():
    """Synthesise the Lambda stack with all plugins (no Docker bundling)."""
    os.environ["CDK_DEFAULT_ACCOUNT"] = "123456789012"
    os.environ["CDK_DEFAULT_REGION"] = "us-east-1"

    app = App(context={"aws:cdk:bundling-stacks": []})

    permissions = OscarPermissionsStack(
        app, "Perms", environment="dev", plugins=ALL_PLUGINS, env=ENV,
    )
    secrets = OscarSecretsStack(
        app, "Secrets", environment="dev", plugins=ALL_PLUGINS, env=ENV,
    )
    storage = OscarStorageStack(app, "Storage", environment="dev", env=ENV)
    vpc = OscarVpcStack(app, "Vpc", env=ENV)

    lambda_stack = OscarLambdaStack(
        app, "Lambda",
        permissions_stack=permissions,
        secrets_stack=secrets,
        storage_stack=storage,
        vpc_stack=vpc,
        environment="dev",
        plugins=ALL_PLUGINS,
        env=ENV,
    )
    return lambda_stack


class TestPluginStackWiring:
    """Verify plugins are wired into the Lambda stack correctly."""

    def test_each_plugin_has_lambda_function(self, stacks):
        """Every plugin name should have an entry in lambda_functions."""
        for plugin in ALL_PLUGINS:
            assert plugin.name in stacks.lambda_functions, \
                f"Plugin '{plugin.name}' missing from lambda_functions"

    def test_jenkins_has_own_lambda(self, stacks):
        """Jenkins should have a separate Lambda from metrics."""
        jenkins_fn = stacks.lambda_functions["jenkins"]
        metrics_fn = stacks.lambda_functions["metrics"]
        assert jenkins_fn is not metrics_fn

    def test_lambda_function_count(self, stacks):
        """Should be 2 plugin entries + 2 core = 4 keys in lambda_functions dict."""
        # 2 plugins + supervisor-agent + communication-handler = 4 entries
        assert len(stacks.lambda_functions) == 4

    def test_lambda_template_function_count(self, stacks):
        """CloudFormation template should have 4 Lambda functions
        (supervisor + communication + jenkins + unified metrics)."""
        template = Template.from_stack(stacks)
        template.resource_count_is("AWS::Lambda::Function", 4)


# ---------------------------------------------------------------------------
# Metrics plugin — no-write guardrail tests
# ---------------------------------------------------------------------------

class TestMetricsNoWriteGuardrail:
    """Ensure the metrics plugin never makes mutating requests to OpenSearch.

    All OpenSearch calls from the metrics Lambda must be read-only (GET).
    These tests statically verify that no code path can issue POST, PUT,
    or DELETE requests.
    """

    def test_iam_policies_exclude_write_actions(self):
        """IAM policies must not grant es:ESHttpPost, es:ESHttpPut, or es:ESHttpDelete."""
        plugin = MetricsPlugin()
        policies = plugin.get_iam_policies("123456789012", "us-east-1", "dev")
        forbidden_actions = {"es:ESHttpPost", "es:ESHttpPut", "es:ESHttpDelete"}
        for stmt in policies:
            stmt_json = stmt.to_json()
            actions = stmt_json.get("Action", [])
            if isinstance(actions, str):
                actions = [actions]
            overlap = forbidden_actions & set(actions)
            assert not overlap, (
                f"Metrics IAM policy grants forbidden actions: {overlap}. "
                f"This plugin must be read-only — no POST/PUT/DELETE on OpenSearch."
            )

    def test_iam_policies_exclude_wildcard_es_actions(self):
        """IAM policies must not grant es:* (blanket OpenSearch access)."""
        plugin = MetricsPlugin()
        policies = plugin.get_iam_policies("123456789012", "us-east-1", "dev")
        for stmt in policies:
            stmt_json = stmt.to_json()
            actions = stmt_json.get("Action", [])
            if isinstance(actions, str):
                actions = [actions]
            assert "es:*" not in actions, (
                "Metrics IAM policy must not grant wildcard es:* access"
            )

    def test_no_post_calls_in_metrics_lambda(self):
        """No code in the metrics Lambda may invoke _make_request or opensearch_request with POST."""
        lambda_dir = os.path.join("plugins", "metrics", "lambda")
        violations = []

        for py_file in glob.glob(os.path.join(lambda_dir, "*.py")):
            with open(py_file) as f:
                source = f.read()
            try:
                tree = ast.parse(source, filename=py_file)
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = None
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                if name not in ("opensearch_request", "_make_request"):
                    continue
                # First positional arg is the HTTP method
                if node.args:
                    method_arg = node.args[0]
                    if isinstance(method_arg, ast.Constant) and method_arg.value == "POST":
                        violations.append(
                            f"{os.path.basename(py_file)}:{node.lineno} "
                            f"{name}('POST', ...) — POST is forbidden"
                        )

        assert not violations, (
            "Metrics Lambda makes POST calls to OpenSearch:\n" +
            "\n".join(violations)
        )

    def test_no_direct_post_put_delete_requests_in_metrics_lambda(self):
        """Metrics Lambda must not call requests.post(), requests.put(), or requests.delete()."""
        lambda_dir = os.path.join("plugins", "metrics", "lambda")
        forbidden = {"post", "put", "delete"}
        violations = []

        for py_file in glob.glob(os.path.join(lambda_dir, "*.py")):
            with open(py_file) as f:
                source = f.read()
            try:
                tree = ast.parse(source, filename=py_file)
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if (isinstance(func, ast.Attribute) and
                        func.attr in forbidden and
                        isinstance(func.value, ast.Name) and
                        func.value.id == "requests"):
                    violations.append(
                        f"{os.path.basename(py_file)}:{node.lineno} "
                        f"calls requests.{func.attr}()"
                    )

        assert not violations, (
            "Metrics Lambda makes forbidden HTTP calls:\n" +
            "\n".join(violations)
        )

    def test_make_request_only_called_with_get(self):
        """_make_request() and opensearch_request() must only be invoked with GET."""
        lambda_dir = os.path.join("plugins", "metrics", "lambda")
        violations = []

        for py_file in glob.glob(os.path.join(lambda_dir, "*.py")):
            with open(py_file) as f:
                source = f.read()
            try:
                tree = ast.parse(source, filename=py_file)
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = None
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                if name not in ("_make_request", "opensearch_request"):
                    continue
                if node.args:
                    method_arg = node.args[0]
                    if isinstance(method_arg, ast.Constant) and method_arg.value != "GET":
                        violations.append(
                            f"{os.path.basename(py_file)}:{node.lineno} "
                            f"{name}('{method_arg.value}', ...) — only GET allowed"
                        )

        assert not violations, (
            "Metrics Lambda uses non-GET HTTP methods:\n" +
            "\n".join(violations)
        )
