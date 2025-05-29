terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# Local values
locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "Terraform"
    Purpose     = "AWS Cost Reporting"
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
# Package Lambda Code
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../sources"
  output_path = "${path.module}/../sources/lambda_function.zip"
}

# S3 bucket for storing execution state
resource "aws_s3_bucket" "cost_reporter_state" {
  bucket = "${var.project_name}-cost-reporter-state-${var.environment}-${random_string.bucket_suffix.result}"

  tags = local.common_tags
}

resource "random_string" "bucket_suffix" {
  length  = 8
  special = false
  upper   = false
}

# IAM Role for Lambda
resource "aws_iam_role" "lambda_role" {
  name = "${var.project_name}-lambda-role-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })

  tags = local.common_tags
}

# IAM Policy for Lambda
resource "aws_iam_policy" "lambda_policy" {
  name        = "${var.project_name}-lambda-policy-${var.environment}"
  description = "IAM policy for AWS Cost Reporter Lambda function"

  policy = jsonencode({
  Version = "2012-10-17"
  Statement = [
    {
      Effect = "Allow"
      Action = [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ]
      Resource = "arn:aws:logs:*:*:*"
    },
    {
      Effect = "Allow"
      Action = [
        "ce:GetCostAndUsage",
        "ce:GetUsageReport",
        "ec2:DescribeRegions",
        "ec2:DescribeInstances",
        "ec2:DescribeVolumes",
        "ec2:DescribeNatGateways",
        "ec2:DescribeVpcEndpoints",
        "ec2:DescribeAddresses",
        "rds:DescribeDBInstances",
        "elasticloadbalancing:DescribeLoadBalancers",
        "elbv2:DescribeLoadBalancers",
        "elasticache:DescribeCacheClusters",
        "redshift:DescribeClusters",
        "lambda:ListFunctions",
        "cloudfront:ListDistributions",
        "route53:ListHostedZones",
        "ses:SendEmail",
        "ses:SendRawEmail",
        "s3:DeleteObject",
        "s3:GetObject",
        "s3:PutObject"
      ]
      Resource = "*"
    }
  ]
})


  tags = local.common_tags
}

# Attach policy to role
resource "aws_iam_role_policy_attachment" "lambda_policy_attachment" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = aws_iam_policy.lambda_policy.arn
}

# CloudWatch Log Group
resource "aws_cloudwatch_log_group" "lambda_logs" {
  name              = "/aws/lambda/${var.project_name}-${var.environment}"
  retention_in_days = 14

  tags = local.common_tags
}

# Lambda Function
resource "aws_lambda_function" "cost_reporter" {
  filename         = data.archive_file.lambda_zip.output_path
  function_name    = "${var.project_name}-${var.environment}"
  role            = aws_iam_role.lambda_role.arn
  handler         = "lambda_function.lambda_handler"
  runtime         = "python3.11"
  timeout         = 900 # 15 minutes
  memory_size     = 512
  source_code_hash = data.archive_file.lambda_zip.output_path

  environment {
    variables = {
      S3_BUCKET_NAME   = aws_s3_bucket.cost_reporter_state.bucket
      SENDER_EMAIL      = var.sender_email
      RECIPIENT_EMAILS  = join(",", var.recipient_emails)
      AWS_REGION_SES    = var.aws_region
      ENVIRONMENT       = var.environment
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.lambda_policy_attachment,
    aws_cloudwatch_log_group.lambda_logs
  ]

  tags = local.common_tags
}

# EventBridge Rule for scheduling (9 AM Vietnamese time = 2 AM UTC)
# cron(Minutes Hours Day-of-month Month Day-of-week Year)
resource "aws_cloudwatch_event_rule" "daily_schedule" {
  count               = var.schedule_enabled ? 1 : 0
  name                = "${var.project_name}-daily-schedule-${var.environment}"
  description         = "Trigger AWS Cost Reporter daily at 9:15 AM Vietnamese time"
  schedule_expression = "cron(30 1 * * ? *)" # 2:15 AM UTC = 9:15 AM GMT+7

  tags = local.common_tags
}

# EventBridge Target
resource "aws_cloudwatch_event_target" "lambda_target" {
  count     = var.schedule_enabled ? 1 : 0
  rule      = aws_cloudwatch_event_rule.daily_schedule[0].name
  target_id = "TriggerLambdaFunction"
  arn       = aws_lambda_function.cost_reporter.arn

  input = jsonencode({
    source      = "eventbridge"
    detail_type = "scheduled_execution"
    time        = "9AM_Vietnam"
  })

  retry_policy {
    maximum_retry_attempts = 2
    maximum_event_age_in_seconds = 3600
  }
}

# Lambda permission for EventBridge
resource "aws_lambda_permission" "allow_eventbridge" {
  count         = var.schedule_enabled ? 1 : 0
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.cost_reporter.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily_schedule[0].arn
}

# SNS Topic for Lambda errors (optional)
resource "aws_sns_topic" "lambda_errors" {
  name = "${var.project_name}-errors-${var.environment}"

  tags = local.common_tags
}

# CloudWatch Alarm for Lambda errors
resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  alarm_name          = "${var.project_name}-lambda-errors-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "1"
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = "300"
  statistic           = "Sum"
  threshold           = "0"
  alarm_description   = "This metric monitors lambda errors"
  alarm_actions       = [aws_sns_topic.lambda_errors.arn]

  dimensions = {
    FunctionName = aws_lambda_function.cost_reporter.function_name
  }

  tags = local.common_tags
}
