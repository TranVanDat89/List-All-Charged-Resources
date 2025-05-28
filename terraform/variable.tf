# Variables
variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "ap-southeast-2" # Singapore region (closest to Vietnam)
}

variable "sender_email" {
  description = "SES verified sender email address"
  type        = string
}

variable "recipient_emails" {
  description = "List of recipient email addresses"
  type        = list(string)
}

variable "project_name" {
  description = "Project name for resource naming"
  type        = string
  default     = "aws-cost-reporter"
}

variable "environment" {
  description = "Environment (dev, staging, prod)"
  type        = string
  default     = "prod"
}

variable "schedule_enabled" {
  description = "Enable/disable the scheduled execution"
  type        = bool
  default     = true
}

variable "sns_email" {
  description = "The email address to subscribe to the SNS topic"
  type        = string
  default = "tranvandatdh012@gmail.com"
}

