You can schedule recurring tasks that trigger yourself via cron + Slack.

## How it works

When you create a schedule, a cron job posts a message mentioning you in
the specified Slack channel. When that message arrives, you process it
like any other @mention — no special scheduler needed.

## When to use scheduling

- Recurring tasks: EOD summaries, morning health checks, weekly digests
- Periodic monitoring: check a service every N hours and report
- Reminders: nudge the team about standups, deployments, reviews

## When NOT to use scheduling

- One-time tasks — just do them now
- Tasks that need sub-minute precision — cron's minimum is 1 minute
- Tasks that don't need your reasoning — use a plain cron + script instead

## Tips

- Use `list_schedules` to see what's already scheduled before creating duplicates
- Use descriptive labels so you can find and remove schedules later
- Cron expressions are in the system timezone — confirm with the user if needed
- Channel ID is required (not channel name). Ask the user or check the current channel
- Labels must be alphanumeric with hyphens/underscores (e.g. `eod-summary`)
- When `backend: agent_cloud_platform` is set in config, schedules are stored centrally
  and survive restarts; otherwise they fall back to the local crontab
