# AWS Cost Reporter

An automated AWS cost reporting solution that scans all AWS resources across all regions and sends daily email reports at 9:00 AM Vietnamese time.

## 🏗️ Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   EventBridge   │────│  Lambda Function │────│   Amazon SES    │
│  (Daily 9 AM)   │    │  (Cost Scanner)  │    │ (Email Service) │
└─────────────────┘    └──────────────────┘    └─────────────────┘
                              │
                              ▼
                    ┌──────────────────┐
                    │  AWS Services    │
                    │ • Cost Explorer  │
                    │ • EC2, RDS, EBS  │
                    │ • ELB, Lambda    │
                    │ • CloudFront     │
                    │ • Route 53       │
                    └──────────────────┘
```

## 📋 Prerequisites

### Tools Required
- [Terraform](https://www.terraform.io/downloads.html) >= 1.0
- [AWS CLI](https://aws.amazon.com/cli/) configured with appropriate credentials
- [jq](https://stedolan.github.io/jq/) (for JSON processing in scripts)

### AWS Permissions
Your AWS user/role needs the following permissions:
- Full access to Lambda, IAM, CloudWatch, EventBridge
- SES send permissions
- Read permissions for all AWS services (for cost scanning)

### SES Setup
1. Go to AWS SES Console
2. Verify your sender email address
3. If in sandbox mode, verify all recipient email addresses
4. For production use, request production access

## 🚀 Quick Start

### 1. Clone and Setup

```bash
# Create project directory
mkdir aws-cost-reporter
cd aws-cost-reporter

# Create Terraform directory
mkdir terraform
```

### 2. Copy Files

Copy the following files to your project:
- `main.tf` → `terraform/main.tf`
- `lambda_function.py` → `sourrces/lambda_function.py`

### 3. Configure Variables

```bash
# Copy example variables file
cp terraform/terraform.tfvars.example terraform/terraform.tfvars

# Edit with your values
vim terraform/terraform.tfvars
```

Example `terraform.tfvars`:
```hcl
aws_region = "ap-southeast-2"
sender_email = "reports@yourcompany.com"
recipient_emails = [
  "admin@yourcompany.com",
  "finance@yourcompany.com"
]
project_name = "aws-cost-reporter"
environment = "prod"
schedule_enabled = true
```

### 4. Deploy

```bash
# Make script executable
chmod +x deploy.sh

# Run deployment
bash deploy.sh
```

## 📧 Email Report Features

### Cost Summary
- Total cost for the last 30 days
- Color-coded cost alerts (green/yellow/red)
- Cost breakdown by AWS service
- Percentage distribution

### Resource Details
- Resources organized by region
- Grouped by service type
- Resource states and configurations
- Instance types, sizes, and specifications

### Professional Layout
- HTML formatted emails
- Plain text alternative
- Mobile-friendly design
- Corporate-ready appearance

## ⏰ Scheduling

The function runs **daily at 9:00 AM Vietnamese time (GMT+7)**:
- **Cron Expression**: `cron(0 2 * * ? *)`
- **UTC Time**: 2:00 AM
- **Vietnamese Time**: 9:00 AM

### Modify Schedule

To change the schedule, update the cron expression in `main.tf`:

```hcl
# Example: Run at 8 AM Vietnamese time
schedule_expression = "cron(0 1 * * ? *)"

# Example: Run twice daily (9 AM and 6 PM Vietnamese time)
schedule_expression = "cron(0 2,11 * * ? *)"
```

## 🔧 Configuration Options

### Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `SENDER_EMAIL` | SES verified sender email | `reports@company.com` |
| `RECIPIENT_EMAILS` | Comma-separated recipient list | `admin@company.com,finance@company.com` |
| `AWS_REGION_SES` | SES region | `ap-southeast-2` |
| `ENVIRONMENT` | Environment name | `prod` |

### Terraform Variables

| Variable | Type | Description | Default |
|----------|------|-------------|---------|
| `aws_region` | string | AWS region for deployment | `ap-southeast-2` |
| `sender_email` | string | SES sender email | Required |
| `recipient_emails` | list(string) | List of recipient emails | Required |
| `project_name` | string | Project name for resources | `aws-cost-reporter` |
| `environment` | string | Environment (dev/staging/prod) | `prod` |
| `schedule_enabled` | bool | Enable/disable scheduling | `true` |

## 📊 Monitored Resources

### Compute Services
- **EC2**: Running and stopped instances
- **Lambda**: All functions (with invocation costs)

### Storage Services
- **EBS**: All volumes (attached and unattached)
- **S3**: Buckets (via Cost Explorer data)

### Database Services
- **RDS**: All database instances
- **ElastiCache**: Cache clusters
- **Redshift**: Data warehouse clusters

### Networking Services
- **ELB**: Application, Network, and Classic Load Balancers
- **VPC**: NAT Gateways
- **CloudFront**: Distributions

### Global Services
- **Route 53**: Hosted zones
- **CloudFront**: CDN distributions

## 🛠️ Management Commands

### Manual Execution
```bash
# Invoke function manually
aws lambda invoke \
  --function-name aws-cost-reporter-prod \
  --payload '{"test": true}' \
  response.json

# View response
cat response.json | jq .
```

### View Logs
```bash
# View recent logs
aws logs tail /aws/lambda/aws-cost-reporter-prod --follow

# View specific log stream
aws logs describe-log-streams \
  --log-group-name /aws/lambda/aws-cost-reporter-prod
```

### Update Function
```bash
# After modifying lambda_function.py
cd terraform
terraform plan
terraform apply
```

## 🔒 Security Best Practices

### IAM Permissions
- Use least-privilege access
- Separate roles for different environments
- Regular permission audits

### Email Security
- Use dedicated email addresses
- Verify all recipient addresses
- Monitor for unauthorized access

### Code Security
- Store sensitive data in environment variables
- Use AWS Secrets Manager for highly sensitive data
- Regular dependency updates

## 🚨 Troubleshooting

### Common Issues

#### 1. SES Email Not Sending
```bash
# Check SES sending quota
aws ses get-send-quota

# Verify email addresses
aws ses list-verified-email-addresses

# Check SES reputation
aws ses get-reputation
```

#### 2. Lambda Timeout
- Increase timeout in `main.tf`:
```hcl
resource "aws_lambda_function" "cost_reporter" {
  timeout = 600  # 10 minutes
  # ...
}
```

#### 3. Permission Errors
- Check CloudWatch logs for specific permission errors
- Verify IAM policies include required services
- Ensure Cost Explorer is enabled

#### 4. Cost Data Not Available
- Enable Cost Explorer in AWS Console
- Wait 24 hours for initial data population
- Check if account has any charges

### Debug Mode

Add debugging to Lambda function:
```python
import logging
logging.getLogger().setLevel(logging.DEBUG)
```

## 📈 Monitoring and Alerts

### CloudWatch Metrics
- Lambda execution duration
- Lambda error count
- SES sending statistics

### Cost Monitoring
- Set up AWS Budget alerts
- Monitor Lambda execution costs
- Track SES sending costs

### Log Analysis
```bash
# Search for errors
aws logs filter-log-events \
  --log-group-name /aws/lambda/aws-cost-reporter-prod \
  --filter-pattern "ERROR"

# Export logs
aws logs create-export-task \
  --log-group-name /aws/lambda/aws-cost-reporter-prod \
  --from $(date -d '1 day ago' +%s)000 \
  --to $(date +%s)000 \
  --destination your-s3-bucket
```

## 🔄 Updates and Maintenance

### Regular Updates
1. Review and update IAM permissions
2. Update Lambda runtime versions
3. Monitor AWS service API changes
4. Review cost optimization opportunities

### Backup and Recovery
```bash
# Export Terraform state
terraform show -json > backup-$(date +%Y%m%d).json

# Backup Lambda function code
aws lambda get-function \
  --function-name aws-cost-reporter-prod \
  --query 'Code.Location' \
  --output text | wget -i -
```