# Outputs
output "lambda_function_name" {
  description = "Name of the Lambda function"
  value       = aws_lambda_function.cost_reporter.function_name
}

output "lambda_function_arn" {
  description = "ARN of the Lambda function"
  value       = aws_lambda_function.cost_reporter.arn
}

output "cloudwatch_log_group" {
  description = "CloudWatch Log Group for Lambda function"
  value       = aws_cloudwatch_log_group.lambda_logs.name
}

output "eventbridge_rule_name" {
  description = "Name of the EventBridge rule"
  value       = var.schedule_enabled ? aws_cloudwatch_event_rule.daily_schedule[0].name : "N/A - Scheduling disabled"
}

output "schedule_expression" {
  description = "Cron expression for the schedule (9 AM Vietnamese time)"
  value       = var.schedule_enabled ? aws_cloudwatch_event_rule.daily_schedule[0].schedule_expression : "N/A - Scheduling disabled"
}

# output "ses_sender_identity" {
#   description = "SES sender email identity"
#   value       = aws_ses_email_identity.sender.email
# }

output "next_execution_time" {
  description = "Next execution time (9 AM Vietnamese time)"
  value       = "Daily at 9:00 AM Vietnamese Time (GMT+7)"
}