# Metrics Plugin

The Metrics plugin gives OSCAR the ability to query OpenSearch metrics data — integration test results, build results, and release readiness — through Slack. It connects to a cross-account OpenSearch cluster via STS AssumeRole and AWS SigV4 authentication.

## How It Works

1. **Cross-Account Access** — The Lambda assumes a role in the OpenSearch account using STS, then signs requests with SigV4.
2. **Query Routing** — Incoming requests are routed by function name to the appropriate handler (integration test, build, or release metrics).
3. **Data Processing** — Results are deduplicated, aggregated, and summarized before being returned to the Bedrock agent.
4. **Sub-Plugins** — Three specialist agents share the same Lambda code but have different action groups and instructions:
   - `metrics-build` — Build results and distribution metrics
   - `metrics-test` — Integration test results and RC-to-build mapping
   - `metrics-release` — Release readiness metrics

## Environment Variables

### Secrets Manager (sensitive — stored in metrics secret)

These values are stored as JSON key-value pairs in a AWS Secrets Manager secret.
The CDK stack creates the secret as `oscar-metrics-build-env-{environment}` (e.g., `oscar-metrics-build-env-dev`).

After deployment, populate it:

```bash
aws secretsmanager put-secret-value \
  --secret-id oscar-metrics-build-env-dev \
  --secret-string '{
    "METRICS_CROSS_ACCOUNT_ROLE_ARN": "arn:aws:iam::your-opensearch-account:role/OpenSearchOscarAccessRole",
    "OPENSEARCH_HOST": "https://your-opensearch-endpoint.region.es.amazonaws.com"
  }'
```

| Key | Description | Example |
|-----|-------------|---------|
| `METRICS_CROSS_ACCOUNT_ROLE_ARN` | IAM role ARN in the OpenSearch account that the Lambda assumes for cross-account access | `arn:aws:iam::123456789012:role/OpenSearchOscarAccessRole` |
| `OPENSEARCH_HOST` | Full URL of the OpenSearch endpoint (include `https://`) | `https://search-metrics.us-east-1.es.amazonaws.com` |

### Secret Format

The metrics secret is stored in **JSON format**. The Lambda reads it via `json.loads()` and extracts individual keys:

```json
{
  "METRICS_CROSS_ACCOUNT_ROLE_ARN": "arn:aws:iam::123456789012:role/OpenSearchOscarAccessRole",
  "OPENSEARCH_HOST": "https://your-opensearch-endpoint.region.es.amazonaws.com"
}
```

The `METRICS_SECRET_NAME` environment variable (automatically set by CDK) tells the Lambda which secret to read.

### CDK Environment Variables (non-sensitive — set via CDK)

These are passed through from `.env` to the Lambda as environment variables. All have sensible defaults.

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENSEARCH_REGION` | AWS region of the OpenSearch cluster | `us-east-1` |
| `OPENSEARCH_SERVICE` | AWS service name for SigV4 signing | `es` |
| `OPENSEARCH_INTEGRATION_TEST_INDEX` | Index pattern for integration test results | `opensearch-integration-test-results-*` |
| `OPENSEARCH_BUILD_RESULTS_INDEX` | Index pattern for build results | `opensearch-distribution-build-results-*` |
| `OPENSEARCH_RELEASE_METRICS_INDEX` | Index name for release metrics | `opensearch_release_metrics` |
| `OPENSEARCH_LARGE_QUERY_SIZE` | Max documents per query | `1000` |
| `OPENSEARCH_REQUEST_TIMEOUT` | Request timeout in seconds | `60` |


## Cross-Account Role Setup

The Lambda needs to assume a role in the OpenSearch account. That role must:

1. Allow the OSCAR Lambda's execution role to assume it (trust policy)
2. Have permissions to query the OpenSearch domain (resource policy)

Store the role ARN in the metrics secret as `METRICS_CROSS_ACCOUNT_ROLE_ARN`.

## Architecture

```
User in Slack
    │
    ▼
Supervisor Agent → routes to Metrics-Specialist
    │
    ▼
Bedrock Agents (3 specialists, shared Lambda)
    │
    ├─ Build-Metrics-Specialist
    │   ├─ get_build_metrics()              ──▶ Build results from OpenSearch
    │   └─ resolve_components_from_builds() ──▶ Build-to-component mapping
    │
    ├─ Test-Metrics-Specialist
    │   ├─ get_integration_test_metrics()   ──▶ Integration test results
    │   └─ get_rc_build_mapping()           ──▶ RC-to-build number mapping
    │
    └─ Release-Metrics-Specialist
        └─ get_release_metrics()            ──▶ Release readiness data
    │
    ▼
Metrics Lambda (shared by all 3 specialists)
    ├─ lambda_function.py    → Bedrock action group handler, routes by function name
    ├─ metrics_handler.py    → Core query orchestration
    └─ Release-Readiness-Metrics-Specialist
        └─ get_release_metrics()            ──▶ Release readiness dataon
    ├─ aws_utils.py          → STS AssumeRole + SigV4 OpenSearch client
    ├─ config.py             → Secrets Manager + env var configuration
    ├─ response_builder.py   → Bedrock response formatting
    ├─ helper_functions.py   → RC mapping, component resolution
    └─ summary_generators.py → Human-readable summary generation
```
