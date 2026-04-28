#!/usr/bin/env python3
"""
Test whether adding collapse at the OpenSearch query level reduces
duplicates compared to relying on post-processing dedup only.

Runs three queries against the integration test index:
1. Plain DSL (no collapse) — shows raw duplicate count
2. DSL with collapse on component.keyword — shows single-field dedup
3. Plain DSL results run through Python dedup — shows multi-field dedup

Usage:
    export OPENSEARCH_HOST=your-host.us-east-1.es.amazonaws.com
    export OPENSEARCH_USERNAME=admin
    export OPENSEARCH_PASSWORD=yourpassword
    python oscar-ai-bot/scripts/test_collapse.py
"""

import json
import os
import sys
from collections import Counter

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HOST = os.environ.get("OPENSEARCH_HOST", "").rstrip("/")
USERNAME = os.environ.get("OPENSEARCH_USERNAME", "admin")
PASSWORD = os.environ.get("OPENSEARCH_PASSWORD", "")
INDEX = os.environ.get(
    "INTEGRATION_TEST_INDEX", "opensearch-integration-test-results-03-2026"
)

if not HOST or not PASSWORD:
    print("ERROR: Set OPENSEARCH_HOST and OPENSEARCH_PASSWORD env vars")
    sys.exit(1)

host = HOST.replace("https://", "").replace("http://", "")
base_url = f"https://{host}"


def search(body):
    url = f"{base_url}/{INDEX}/_search"
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


# Base query: version 2.19.5, RC 1
base_query = {
    "bool": {
        "filter": [
            {"term": {"version.keyword": "2.19.5"}},
            {"term": {"rc_number": 1}},
        ]
    }
}

print("=" * 70)
print("TEST 1: Plain query (no collapse)")
print("-" * 70)
result1 = search({"size": 1000, "query": base_query})
hits1 = result1["hits"]["hits"]
total1 = result1["hits"]["total"]["value"]
print(f"  Total matching docs: {total1}")
print(f"  Returned hits: {len(hits1)}")

# Count duplicates by component
components1 = [h["_source"].get("component", "?") for h in hits1]
dupes1 = {k: v for k, v in Counter(components1).items() if v > 1}
print(f"  Unique components: {len(set(components1))}")
print(f"  Components with duplicates: {len(dupes1)}")
if dupes1:
    top5 = sorted(dupes1.items(), key=lambda x: -x[1])[:5]
    for comp, count in top5:
        print(f"    {comp}: {count} entries")

print()
print("=" * 70)
print("TEST 2: Query with collapse on component.keyword")
print("-" * 70)
result2 = search({
    "size": 1000,
    "query": base_query,
    "collapse": {
        "field": "component.keyword",
        "inner_hits": {
            "name": "latest",
            "size": 1,
            "sort": [{"build_start_time": {"order": "desc"}}],
        },
    },
})
hits2 = result2["hits"]["hits"]
print(f"  Returned hits (collapsed): {len(hits2)}")
components2 = [h["_source"].get("component", "?") for h in hits2]
print(f"  Unique components: {len(set(components2))}")

print()
print("=" * 70)
print("TEST 3: Python multi-field dedup (what data_processors.py does)")
print("-" * 70)

# Simulate deduplicate_integration_test_results logic
groups = {}
for h in hits1:
    s = h["_source"]
    key = (
        s.get("component"),
        str(s.get("version")),
        str(s.get("rc_number")),
        str(s.get("platform")),
        str(s.get("architecture")),
        str(s.get("distribution")),
    )
    bst = s.get("build_start_time")
    if key not in groups:
        groups[key] = s
    else:
        existing_bst = groups[key].get("build_start_time")
        if bst and existing_bst:
            try:
                if int(bst) > int(existing_bst):
                    groups[key] = s
            except (ValueError, TypeError):
                pass

deduped = list(groups.values())
print(f"  After multi-field dedup: {len(deduped)}")
deduped_components = [d.get("component", "?") for d in deduped]
print(f"  Unique components: {len(set(deduped_components))}")

print()
print("=" * 70)
print("COMPARISON")
print("-" * 70)
print(f"  Raw hits:              {len(hits1)}")
print(f"  After collapse:        {len(hits2)} (single-field, component only)")
print(f"  After Python dedup:    {len(deduped)} (multi-field)")
print()
if len(hits2) < len(deduped):
    print("  ⚠ Collapse is MORE aggressive — it loses per-platform/arch/distro detail")
    print("    that the Bedrock agent needs for accurate analysis.")
elif len(hits2) == len(deduped):
    print("  ✓ Collapse and Python dedup produce same count (unlikely for this data)")
else:
    print("  Collapse returns MORE results than Python dedup (unexpected)")
print("=" * 70)
