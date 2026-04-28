#!/usr/bin/env python3
"""
Verify that the agentic search pipeline's QueryPlanningTool generates
size=10 by default (without our NL prompt hint).

Usage:
    export OPENSEARCH_HOST=your-host.us-east-1.es.amazonaws.com
    export OPENSEARCH_USERNAME=admin
    export OPENSEARCH_PASSWORD=yourpassword
    python scripts/verify_agentic_default_size.py

Optionally override:
    export AGENTIC_PIPELINE=metrics-agentic-pipeline
    export INTEGRATION_TEST_INDEX=opensearch-integration-test-results-03-2026
"""

import json
import os
import sys

import requests
import urllib3

# Suppress InsecureRequestWarning for self-signed certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HOST = os.environ.get("OPENSEARCH_HOST", "").rstrip("/")
USERNAME = os.environ.get("OPENSEARCH_USERNAME", "admin")
PASSWORD = os.environ.get("OPENSEARCH_PASSWORD", "")
PIPELINE = os.environ.get("AGENTIC_PIPELINE", "metrics-agentic-pipeline")
INDEX = os.environ.get(
    "INTEGRATION_TEST_INDEX", "opensearch-integration-test-results-03-2026"
)

if not HOST or not PASSWORD:
    print("ERROR: Set OPENSEARCH_HOST and OPENSEARCH_PASSWORD env vars")
    sys.exit(1)

host = HOST.replace("https://", "").replace("http://", "")
base_url = f"https://{host}"

# Query WITHOUT size hint — should produce size=10 in generated DSL
query_without_hint = "Show integration test results for RC 1 for version 2.19.5"

# Query WITH size hint — should produce size=1000 in generated DSL
query_with_hint = (
    "Show integration test results for RC 1 for version 2.19.5"
    ". Use size 1000 in the query."
)
query_build = "Show me build results for RC 1 for version 2.19.5"



def run_agentic_query(query_text: str) -> dict:
    url = f"{base_url}/{INDEX}/_search?search_pipeline={PIPELINE}"
    body = {"size": 1000, "query": {"agentic": {"query_text": query_text}}}
    resp = requests.get(
        url,
        json=body,
        auth=(USERNAME, PASSWORD),
        headers={"Content-Type": "application/json"},
        timeout=30,
        verify=False,
    )
    resp.raise_for_status()
    return resp.json()


def extract_dsl_size(result: dict) -> int | None:
    dsl = result.get("ext", {}).get("dsl_query")
    if not dsl:
        return None
    parsed = json.loads(dsl) if isinstance(dsl, str) else dsl
    return parsed.get("size")


print("=" * 60)
print("Test 1: Agentic query WITHOUT size hint")
print(f"  Query: {query_without_hint}")
print("-" * 60)
result1 = run_agentic_query(query_without_hint)
size1 = extract_dsl_size(result1)
hits1 = len(result1.get("hits", {}).get("hits", []))
total1 = result1.get("hits", {}).get("total", {}).get("value", "?")
dsl1 = result1.get("ext", {}).get("dsl_query", "N/A")
print(f"  Generated DSL size: {size1}")
print(f"  Hits returned: {hits1}")
print(f"  Total matching: {total1}")
print(f"  Generated DSL: {dsl1}")

print()
print("=" * 60)
print("Test 2: Agentic query WITH size hint")
print(f"  Query: {query_with_hint}")
print("-" * 60)
result2 = run_agentic_query(query_with_hint)
size2 = extract_dsl_size(result2)
hits2 = len(result2.get("hits", {}).get("hits", []))
total2 = result2.get("hits", {}).get("total", {}).get("value", "?")
dsl2 = result2.get("ext", {}).get("dsl_query", "N/A")
print(f"  Generated DSL size: {size2}")
print(f"  Hits returned: {hits2}")
print(f"  Total matching: {total2}")
print(f"  Generated DSL: {dsl2}")

result3 = run_agentic_query(query_build)
size3 = extract_dsl_size(result2)
hits3 = len(result3.get("hits", {}).get("hits", []))
total3 = result3.get("hits", {}).get("total", {}).get("value", "?")
dsl3 = result3.get("ext", {}).get("dsl_query", "N/A")
print(f"  Generated DSL size: {size3}")
print(f"  Hits returned: {hits3}")
print(f"  Total matching: {total3}")
print(f"  Generated DSL: {dsl3}")

