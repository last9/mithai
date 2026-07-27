You can inspect Kubernetes clusters with safe, read-only kubectl operations.

Use this skill when the user asks about pods, deployments, recent events,
workload status, or container logs.

Rules:

- Only inspect resources. This skill does not create, update, restart, scale, or delete anything.
- Prefer `get_pods` before fetching logs so you know the exact pod and namespace.
- Use `get_events` when diagnosing Pending, CrashLoopBackOff, ImagePullBackOff, or scheduling issues.
- Use `describe_resource` when a concise object summary is not enough.
- Include namespace, pod names, phases, restart counts, and relevant event reasons in your answer.
- If kubectl returns an error, report it plainly and do not invent cluster state.
