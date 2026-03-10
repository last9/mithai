Use slack_get_history to read recent messages from a Slack channel.

Useful when joining a new channel, when asked about recent activity,
or when you need context about what a channel has been discussing.
The result includes formatted messages and a user ID → name map so
you can @mention people correctly.

Use slack_send_message to proactively post a message to a Slack channel or thread.
Useful for sending summaries, alerts, or pinging teammates without waiting for
someone to ask. Requires approval unless the operator has configured auto-approve.
Always specify channel_id; use thread_ts to reply in a thread.
