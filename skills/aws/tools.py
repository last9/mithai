"""Skill: AWS infrastructure — aggregated, LLM-friendly tools.

Complements shell access. These tools handle multi-step API operations and
return compact, pre-parsed output instead of raw AWS JSON.

Config (skills.config.aws in config.yaml):
    region: us-east-1
    profile: my-profile   # optional AWS CLI named profile

Heartbeat-safe tools (add to heartbeat.auto_approve):
    - aws__get_infrastructure_summary
    - aws__get_alarms
    - aws__get_metrics
    - aws__get_instance_detail
    - aws__get_costs
"""

import json
import subprocess
from datetime import date, datetime, timedelta, timezone


TOOLS = [
    {
        "name": "get_infrastructure_summary",
        "description": (
            "Aggregate snapshot: EC2 instances, firing CloudWatch alarms, load balancers, "
            "and month-to-date cost in one compact response. "
            "Use for general health checks or heartbeat monitoring."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "region": {"type": "string", "description": "AWS region. Defaults to configured region."},
            },
        },
    },
    {
        "name": "get_alarms",
        "description": (
            "CloudWatch alarms filtered by state (ALARM, INSUFFICIENT_DATA, OK, or all). "
            "Returns name, metric, threshold, and how long each has been in that state."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "state": {"type": "string", "description": "ALARM, INSUFFICIENT_DATA, OK, or all. Default: ALARM."},
                "prefix": {"type": "string", "description": "Filter alarm names by prefix."},
                "region": {"type": "string"},
            },
        },
    },
    {
        "name": "get_metrics",
        "description": (
            "CloudWatch metric with avg, peak, and trend direction (rising/falling/stable). "
            "Reduces raw datapoints to a one-line summary."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "description": "e.g. AWS/EC2, AWS/RDS, AWS/ApplicationELB"},
                "metric_name": {"type": "string", "description": "e.g. CPUUtilization, TargetResponseTime"},
                "dimensions": {"type": "object", "description": "e.g. {\"InstanceId\": \"i-abc\"}"},
                "period_minutes": {"type": "integer", "description": "Window in minutes. Default: 60."},
                "region": {"type": "string"},
            },
            "required": ["namespace", "metric_name", "dimensions"],
        },
    },
    {
        "name": "get_instance_detail",
        "description": (
            "Operational details for a single EC2 instance: state, type, IPs, security groups, "
            "tags, IAM role, EBS volumes, and current CPU. "
            "Extracts the useful fields from describe-instances' deeply nested JSON."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "instance_id": {"type": "string", "description": "EC2 instance ID, e.g. i-0abc123."},
                "region": {"type": "string"},
            },
            "required": ["instance_id"],
        },
    },
    {
        "name": "get_costs",
        "description": (
            "AWS costs for the last N days broken down by service, sorted descending. "
            "Requires Cost Explorer to be enabled in the account."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Look-back window. Default: 30."},
                "region": {"type": "string"},
            },
        },
    },
    {
        "name": "change_instance_state",
        "description": (
            "Start, stop, or reboot an EC2 instance. "
            "Validates current state before acting. Always requires human approval."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "instance_id": {"type": "string"},
                "action": {"type": "string", "description": "start, stop, or reboot"},
                "reason": {"type": "string", "description": "Optional reason for audit."},
                "region": {"type": "string"},
            },
            "required": ["instance_id", "action"],
        },
        "human": "approve",
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _aws(*args, timeout=30) -> dict:
    try:
        result = subprocess.run(
            ["aws", "--no-cli-pager", *args, "--output", "json"],
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


def _base_args(region: str, profile: str | None) -> list[str]:
    args = ["--region", region]
    if profile:
        args += ["--profile", profile]
    return args


def _relative_time(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        if delta.days >= 1:
            return f"{delta.days}d ago"
        hours = delta.seconds // 3600
        if hours >= 1:
            return f"{hours}h ago"
        return f"{delta.seconds // 60}m ago"
    except Exception:
        return iso_str


def _flatten_instances(data: dict) -> list[dict]:
    instances = []
    for reservation in data.get("Reservations", []):
        for inst in reservation.get("Instances", []):
            name = next(
                (t["Value"] for t in inst.get("Tags", []) if t["Key"] == "Name"),
                inst["InstanceId"],
            )
            instances.append({
                "id": inst["InstanceId"],
                "name": name,
                "state": inst["State"]["Name"],
                "type": inst.get("InstanceType", ""),
                "ip": inst.get("PrivateIpAddress", ""),
                "public_ip": inst.get("PublicIpAddress", ""),
                "az": inst.get("Placement", {}).get("AvailabilityZone", ""),
                "launched": _relative_time(inst.get("LaunchTime", "")),
                "raw": inst,
            })
    return instances


def _format_alarms(alarms: list[dict], state: str) -> str:
    if not alarms:
        return f"No alarms in {state} state."
    lines = [f"CloudWatch Alarms — {state} ({len(alarms)}):"]
    for a in alarms:
        dims = ", ".join(f"{d['Name']}={d['Value']}" for d in a.get("Dimensions", []))
        op = (a.get("ComparisonOperator", "")
              .replace("GreaterThanOrEqualToThreshold", ">=")
              .replace("GreaterThanThreshold", ">")
              .replace("LessThanOrEqualToThreshold", "<=")
              .replace("LessThanThreshold", "<"))
        changed = _relative_time(a.get("StateUpdatedTimestamp", ""))
        reason = a.get("StateReason", "")[:120]
        lines.append(f"  {a['AlarmName']}")
        lines.append(f"    {a.get('Namespace','')} {a.get('MetricName','')} {op} {a.get('Threshold','')} [{dims}]")
        lines.append(f"    Since: {changed} | {reason}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def handle(name: str, input: dict, ctx: dict) -> str:
    config = ctx.get("config", {})
    region = input.get("region") or config.get("region", "us-east-1")
    profile = config.get("profile")
    base = _base_args(region, profile)

    if name == "get_infrastructure_summary":
        parts = []

        ec2 = _aws("ec2", "describe-instances", *base)
        if "error" not in ec2:
            instances = _flatten_instances(ec2)
            by_state: dict[str, list] = {}
            for i in instances:
                by_state.setdefault(i["state"], []).append(i)
            lines = [f"EC2 ({len(instances)} instance{'s' if len(instances) != 1 else ''}):"]
            for state, group in sorted(by_state.items()):
                compact = [f"{i['id']} [{i['name']}, {i['type']}, {i['ip']}]" for i in group]
                lines.append(f"  {state.upper()} ({len(group)}): {', '.join(compact)}")
            parts.append("\n".join(lines))
        else:
            parts.append(f"EC2: {ec2['error']}")

        alarms = _aws("cloudwatch", "describe-alarms", "--state-value", "ALARM", *base)
        if "error" not in alarms:
            parts.append(_format_alarms(alarms.get("MetricAlarms", []), "ALARM"))
        else:
            parts.append(f"Alarms: {alarms['error']}")

        elb = _aws("elbv2", "describe-load-balancers", *base)
        if "error" not in elb and elb.get("LoadBalancers"):
            lb_lines = ["Load Balancers:"]
            for lb in elb["LoadBalancers"]:
                lb_lines.append(
                    f"  {lb['LoadBalancerName']} [{lb['Type']}, {lb['State']['Code']}]"
                    f" — {lb['DNSName']}"
                )
            parts.append("\n".join(lb_lines))

        today = date.today()
        cost = _aws(
            "ce", "get-cost-and-usage",
            "--time-period", f"Start={today.replace(day=1).isoformat()},End={today.isoformat()}",
            "--granularity", "MONTHLY",
            "--metrics", "UnblendedCost",
            *base,
        )
        if "error" in cost:
            parts.append(f"Cost MTD: unavailable ({cost['error']})")
        else:
            try:
                total = sum(
                    float(p["Total"]["UnblendedCost"]["Amount"])
                    for p in cost.get("ResultsByTime", [])
                )
                parts.append(f"Cost MTD: ${total:.2f}")
            except Exception:
                parts.append("Cost MTD: unavailable (parse error)")

        return "\n\n".join(parts)

    elif name == "get_alarms":
        state = input.get("state", "ALARM").upper()
        prefix = input.get("prefix", "")

        if state == "ALL":
            data = _aws("cloudwatch", "describe-alarms", *base)
        else:
            data = _aws("cloudwatch", "describe-alarms", "--state-value", state, *base)

        if "error" in data:
            return json.dumps(data)

        alarms = data.get("MetricAlarms", [])
        if prefix:
            alarms = [a for a in alarms if a["AlarmName"].startswith(prefix)]

        return _format_alarms(alarms, state)

    elif name == "get_metrics":
        namespace = input["namespace"]
        metric_name = input["metric_name"]
        dimensions = input.get("dimensions", {})
        period_minutes = input.get("period_minutes", 60)

        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=period_minutes)
        granularity = max(60, (period_minutes * 60) // 10)

        dim_args = []
        for k, v in dimensions.items():
            dim_args += ["--dimensions", f"Name={k},Value={v}"]

        data = _aws(
            "cloudwatch", "get-metric-statistics",
            "--namespace", namespace,
            "--metric-name", metric_name,
            "--start-time", start.isoformat(),
            "--end-time", end.isoformat(),
            "--period", str(granularity),
            "--statistics", "Average", "Maximum", "Minimum",
            *dim_args, *base,
        )
        if "error" in data:
            return json.dumps(data)

        datapoints = sorted(data.get("Datapoints", []), key=lambda d: d["Timestamp"])
        if not datapoints:
            return f"No data for {namespace}/{metric_name} in the last {period_minutes}m."

        avgs = [d.get("Average", 0) for d in datapoints]
        avg = round(sum(avgs) / len(avgs), 2)
        peak = round(max(d.get("Maximum", 0) for d in datapoints), 2)
        low = round(min(d.get("Minimum", 0) for d in datapoints), 2)
        current = round(avgs[-1], 2)

        mid = len(avgs) // 2
        trend = "stable"
        if mid > 0:
            first = sum(avgs[:mid]) / mid
            second = sum(avgs[mid:]) / (len(avgs) - mid)
            if first > 0:
                if second > first * 1.1:
                    trend = f"RISING (+{round((second/first - 1)*100)}% vs first half)"
                elif second < first * 0.9:
                    trend = f"FALLING (-{round((1 - second/first)*100)}% vs first half)"

        return (
            f"{namespace} {metric_name} — last {period_minutes}m\n"
            f"  Current: {current} | Mean: {avg} | Peak: {peak} | Low: {low}\n"
            f"  Trend: {trend}"
        )

    elif name == "get_instance_detail":
        instance_id = input["instance_id"]
        data = _aws("ec2", "describe-instances", "--instance-ids", instance_id, *base)
        if "error" in data:
            return json.dumps(data)

        instances = _flatten_instances(data)
        if not instances:
            return json.dumps({"error": f"Instance {instance_id} not found"})

        inst = instances[0]
        raw = inst["raw"]

        sg_names = [sg.get("GroupName", sg["GroupId"]) for sg in raw.get("SecurityGroups", [])]
        role = raw.get("IamInstanceProfile", {}).get("Arn", "").split("/")[-1] or "none"
        tags = {t["Key"]: t["Value"] for t in raw.get("Tags", []) if t["Key"] != "Name"}
        volumes = [
            f"{m['Ebs']['VolumeId']} [{m['DeviceName']}]"
            for m in raw.get("BlockDeviceMappings", []) if "Ebs" in m
        ]

        # CPU last 5m
        end = datetime.now(timezone.utc)
        cpu_data = _aws(
            "cloudwatch", "get-metric-statistics",
            "--namespace", "AWS/EC2",
            "--metric-name", "CPUUtilization",
            "--dimensions", f"Name=InstanceId,Value={instance_id}",
            "--start-time", (end - timedelta(minutes=5)).isoformat(),
            "--end-time", end.isoformat(),
            "--period", "300",
            "--statistics", "Average",
            *base,
        )
        cpu_str = "n/a"
        if "error" not in cpu_data and cpu_data.get("Datapoints"):
            latest = sorted(cpu_data["Datapoints"], key=lambda d: d["Timestamp"])[-1]
            cpu_str = f"{round(latest['Average'], 1)}%"

        public = f", {inst['public_ip']} (public)" if inst["public_ip"] else ""
        lines = [
            f"{inst['id']} [{inst['name']}]",
            f"  State: {inst['state']} | Type: {inst['type']} | AZ: {inst['az']}",
            f"  Launched: {inst['launched']} | IP: {inst['ip']} (private){public}",
            f"  CPU (last 5m): {cpu_str}",
            f"  IAM Role: {role}",
            f"  Security Groups: {', '.join(sg_names) or 'none'}",
            f"  Tags: {', '.join(f'{k}={v}' for k, v in tags.items()) or 'none'}",
            f"  EBS: {', '.join(volumes) or 'none'}",
        ]
        return "\n".join(lines)

    elif name == "get_costs":
        days = input.get("days", 30)
        end = date.today()
        start = end - timedelta(days=days)

        data = _aws(
            "ce", "get-cost-and-usage",
            "--time-period", f"Start={start.isoformat()},End={end.isoformat()}",
            "--granularity", "MONTHLY",
            "--metrics", "UnblendedCost",
            "--group-by", "Type=DIMENSION,Key=SERVICE",
            *base,
        )
        if "error" in data:
            return json.dumps(data)

        totals: dict[str, float] = {}
        for period in data.get("ResultsByTime", []):
            for group in period.get("Groups", []):
                svc = group["Keys"][0]
                totals[svc] = totals.get(svc, 0) + float(group["Metrics"]["UnblendedCost"]["Amount"])

        grand_total = sum(totals.values())
        if grand_total == 0:
            return f"No costs found for the last {days} days."

        lines = [f"AWS Cost — last {days}d ({start.isoformat()} to {end.isoformat()}):"]
        other = 0.0
        for svc, amount in sorted(totals.items(), key=lambda x: x[1], reverse=True):
            pct = (amount / grand_total) * 100
            if pct < 1.0:
                other += amount
            else:
                lines.append(f"  {svc:<35} ${amount:>8.2f}  ({pct:.1f}%)")
        if other > 0:
            lines.append(f"  {'Other':<35} ${other:>8.2f}  ({(other/grand_total)*100:.1f}%)")
        lines.append(f"\n  {'Total':<35} ${grand_total:>8.2f}")
        return "\n".join(lines)

    elif name == "change_instance_state":
        instance_id = input["instance_id"]
        action = input.get("action", "").lower()
        reason = input.get("reason", "")

        if action not in ("start", "stop", "reboot"):
            return json.dumps({"error": f"Unknown action '{action}'. Use start, stop, or reboot."})

        data = _aws("ec2", "describe-instances", "--instance-ids", instance_id, *base)
        if "error" in data:
            return json.dumps(data)

        instances = _flatten_instances(data)
        if not instances:
            return json.dumps({"error": f"Instance {instance_id} not found"})

        current = instances[0]["state"]
        name_tag = instances[0]["name"]
        required = {"start": "stopped", "stop": "running", "reboot": "running"}

        if current != required[action]:
            return json.dumps({
                "error": f"Cannot {action} {instance_id} [{name_tag}] — state is {current}, expected {required[action]}"
            })

        cmd = {"start": "start-instances", "stop": "stop-instances", "reboot": "reboot-instances"}
        result = _aws("ec2", cmd[action], "--instance-ids", instance_id, *base)
        if "error" in result:
            return json.dumps(result)

        lines = [f"{instance_id} [{name_tag}]: {current} → {action}ing"]
        if reason:
            lines.append(f"Reason: {reason}")
        return "\n".join(lines)

    return json.dumps({"error": f"Unknown tool: {name}"})
