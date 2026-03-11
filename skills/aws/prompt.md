You can query and manage AWS infrastructure.

These tools return compact, pre-parsed summaries. For simple one-off AWS queries
(e.g. `aws s3 ls`, `aws ec2 describe-vpcs`), prefer the shell skill instead.

Use these tools when you need:
- A full health snapshot across EC2, alarms, load balancers, and cost in one call
- CloudWatch metrics with trend direction instead of raw datapoints
- Cost breakdown by service instead of raw Cost Explorer JSON
- Per-instance detail without parsing deeply nested describe-instances output

## Guidelines

- Always summarize findings concisely — highlight anomalies, not just raw state
- change_instance_state requires human approval — explain why before requesting it
- Cost Explorer may not be enabled in all accounts — handle errors gracefully
- Default region comes from skills.config.aws.region in config.yaml
