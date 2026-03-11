"""Skill: AWS infrastructure queries via AWS CLI."""

import json
import subprocess


TOOLS = [
    {
        "name": "list_instances",
        "description": "List EC2 instances with their state, type, and IPs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "state": {
                    "type": "string",
                    "description": "Filter by state (running, stopped, all)",
                    "default": "all",
                },
                "region": {
                    "type": "string",
                    "description": "AWS region to query (e.g. us-west-2). Defaults to configured region.",
                },
            },
        },
    },
    {
        "name": "list_buckets",
        "description": "List S3 buckets.",
        "input_schema": {
            "type": "object",
            "properties": {
                "region": {
                    "type": "string",
                    "description": "AWS region to query. Defaults to configured region.",
                },
            },
        },
    },
    {
        "name": "get_costs",
        "description": "Get estimated costs for the current month.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Look back N days (default: 30)",
                    "default": 30,
                },
                "region": {
                    "type": "string",
                    "description": "AWS region to query. Defaults to configured region.",
                },
            },
        },
    },
    {
        "name": "stop_instance",
        "description": "Stop an EC2 instance.",
        "input_schema": {
            "type": "object",
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": "EC2 instance ID (e.g., i-0123456789abcdef0)",
                },
                "region": {
                    "type": "string",
                    "description": "AWS region where the instance lives. Defaults to configured region.",
                },
            },
            "required": ["instance_id"],
        },
        "human": "approve",
    },
]


def _aws(*args, timeout=30) -> dict:
    """Run an AWS CLI command and return structured result."""
    try:
        result = subprocess.run(
            ["aws", *args, "--output", "json"],
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip()}
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"output": result.stdout.strip()}
    except FileNotFoundError:
        return {"error": "AWS CLI not found. Is it installed and in PATH?"}
    except subprocess.TimeoutExpired:
        return {"error": f"AWS CLI timed out after {timeout}s"}


def handle(name: str, input: dict, ctx: dict) -> str:
    config = ctx.get("config", {})
    region = input.get("region") or config.get("region", "us-east-1")
    profile = config.get("profile")

    base_args = ["--region", region]
    if profile:
        base_args += ["--profile", profile]

    if name == "list_instances":
        state = input.get("state", "all")
        args = ["ec2", "describe-instances", *base_args]
        if state != "all":
            args += ["--filters", f"Name=instance-state-name,Values={state}"]
        result = _aws(*args)
        return json.dumps(result)

    elif name == "list_buckets":
        result = _aws("s3api", "list-buckets", *base_args)
        return json.dumps(result)

    elif name == "get_costs":
        from datetime import date, timedelta

        days = input.get("days", 30)
        end = date.today()
        start = end - timedelta(days=days)
        result = _aws(
            "ce", "get-cost-and-usage",
            "--time-period", f"Start={start.isoformat()},End={end.isoformat()}",
            "--granularity", "MONTHLY",
            "--metrics", "UnblendedCost",
            *base_args,
        )
        return json.dumps(result)

    elif name == "stop_instance":
        instance_id = input["instance_id"]
        result = _aws("ec2", "stop-instances", "--instance-ids", instance_id, *base_args)
        return json.dumps(result)

    return json.dumps({"error": f"Unknown tool: {name}"})
