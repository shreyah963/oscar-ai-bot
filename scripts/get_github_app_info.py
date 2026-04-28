#!/usr/bin/env python3
"""Get GitHub App installation info using the private key."""

import json
import time

import jwt
import requests

APP_ID = "3330179"
PRIVATE_KEY_PATH = "/Users/shreyybh/Downloads/oscar-github-agent-test.2026-04-09.private-key.pem"

def main():
    with open(PRIVATE_KEY_PATH, "r") as f:
        private_key = f.read()

    now = int(time.time())
    payload = {"iat": now - 60, "exp": now + (10 * 60), "iss": APP_ID}
    token = jwt.encode(payload, private_key, algorithm="RS256")

    resp = requests.get(
        "https://api.github.com/app/installations",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
    )
    resp.raise_for_status()

    for inst in resp.json():
        print(f"Installation ID: {inst['id']}")
        print(f"Account: {inst['account']['login']}")
        print(f"Target type: {inst['target_type']}")
        print("---")

if __name__ == "__main__":
    main()
