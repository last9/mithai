"""Skill: HTTP health checker."""

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
    {
        "name": "check_multiple",
        "description": "Check multiple URLs and return a summary of their health status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of URLs to check",
                },
            },
            "required": ["urls"],
        },
    },
]


def _check_one(url: str) -> dict:
    start = time.time()
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            elapsed = round((time.time() - start) * 1000)
            return {
                "url": url,
                "status": resp.status,
                "response_time_ms": elapsed,
                "healthy": 200 <= resp.status < 400,
            }
    except urllib.error.URLError as e:
        elapsed = round((time.time() - start) * 1000)
        return {
            "url": url,
            "error": str(e.reason),
            "response_time_ms": elapsed,
            "healthy": False,
        }
    except Exception as e:
        return {"url": url, "error": str(e), "healthy": False}


def handle(name: str, input: dict, ctx: dict) -> str:
    if name == "check_url":
        return json.dumps(_check_one(input["url"]))

    elif name == "check_multiple":
        results = [_check_one(url) for url in input["urls"]]
        healthy = sum(1 for r in results if r["healthy"])
        return json.dumps({
            "results": results,
            "summary": f"{healthy}/{len(results)} healthy",
        })

    return json.dumps({"error": f"Unknown tool: {name}"})
