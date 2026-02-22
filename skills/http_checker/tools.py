"""Example skill: HTTP health checker."""

import json
import time
import urllib.request
import urllib.error


TOOLS = [
    {
        "name": "check_url",
        "description": "Check if a URL is reachable and return its status code and response time.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to check (e.g., https://example.com)",
                },
            },
            "required": ["url"],
        },
    },
]


def handle(name: str, input: dict, ctx: dict) -> str:
    if name == "check_url":
        url = input["url"]
        start = time.time()
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                elapsed = round((time.time() - start) * 1000)
                return json.dumps({
                    "url": url,
                    "status": resp.status,
                    "response_time_ms": elapsed,
                    "healthy": 200 <= resp.status < 400,
                })
        except urllib.error.URLError as e:
            elapsed = round((time.time() - start) * 1000)
            return json.dumps({
                "url": url,
                "error": str(e.reason),
                "response_time_ms": elapsed,
                "healthy": False,
            })
        except Exception as e:
            return json.dumps({"url": url, "error": str(e), "healthy": False})

    return json.dumps({"error": f"Unknown tool: {name}"})
