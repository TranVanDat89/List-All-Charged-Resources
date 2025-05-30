import json
import boto3 # type: ignore
from datetime import datetime, timedelta
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from botocore.exceptions import ClientError # type: ignore
import logging
import hashlib
import os

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

MAX_WORKERS = min(32, (os.cpu_count() or 1) * 4)
# Email configuration from environment variables
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'your-sender@example.com')
RECIPIENT_EMAILS = os.environ.get('RECIPIENT_EMAILS', '').split(',')
AWS_REGION_SES = os.environ.get('AWS_REGION_SES', 'ap-southeast-2')
BUCKET_NAME = os.environ.get('S3_BUCKET_NAME', 'aws-cost-reporter-state')

def lambda_handler(event, context):
    """
    Lambda function to list all charged AWS resources across all regions
    """
    try:
        # Create execution ID for idempotency
        execution_date = datetime.now().strftime("%Y-%m-%d")
        execution_id = f"cost-report-{execution_date}"
        
        logger.info(f"Starting cost report execution: {execution_id}")
        # Check if already processed today
        if already_processed_today(execution_id):
            logger.info(f"Report already sent today: {execution_id}")
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'Report already sent today',
                    'execution_id': execution_id,
                    'timestamp': datetime.now().isoformat()
                })
            }
        # Initialize the response structure
        charged_resources = {
            'timestamp': datetime.now().isoformat(),
            'execution_id': execution_id,
            'total_cost': 0.0,
            'resources_by_service': {},
            'detailed_cost_breakdown': {},
            'resources_by_region': {},
            'detailed_resources': [],
            'processing_stats': {
                'regions_checked': 0,
                'resources_found': 0,
                'processing_time_seconds': 0
            }
        }
        
        start_time = datetime.now()
        
        # Get cost data from Cost Explorer with detailed breakdown
        logger.info("Fetching cost data from Cost Explorer")
        cost_data = get_detailed_cost_explorer_data()
        charged_resources['total_cost'] = cost_data['total_cost']
        charged_resources['resources_by_service'] = cost_data['by_service']
        charged_resources['detailed_cost_breakdown'] = cost_data['detailed_breakdown']
        
        # Get detailed resource information for services with charges
        charged_services = set(cost_data['by_service'].keys())
        logger.info(f"Found {len(charged_services)} services with charges: {list(charged_services)}")

        if charged_services:
            # Get resources in parallel
            region_resources, global_resources = get_all_charged_resources(charged_services)
            
            charged_resources['resources_by_region'] = region_resources
            charged_resources['detailed_resources'].extend(global_resources)
            
            # Flatten all region resources
            for region, resources in region_resources.items():
                charged_resources['detailed_resources'].extend(resources)
        
        # Calculate processing stats
        end_time = datetime.now()
        processing_time = (end_time - start_time).total_seconds()
        charged_resources['processing_stats'] = {
            'regions_checked': len(charged_resources['resources_by_region']),
            'resources_found': len(charged_resources['detailed_resources']),
            'processing_time_seconds': round(processing_time, 2)
        }
        
        logger.info(f"Processing completed in {processing_time:.2f} seconds")
        
        # Mark as processed BEFORE sending email to prevent duplicates
        mark_as_processed(execution_id, charged_resources)

        # Send email report
        email_sent = send_email_report(charged_resources)
        charged_resources['email_sent'] = email_sent
        logger.info(f"Cost report completed successfully. Email sent: {email_sent}")
        return {
            'statusCode': 200,
            'body': json.dumps(charged_resources, indent=2, default=str)
        }
        
    except Exception as e:
        logger.error(f"Error in lambda_handler: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e),
                'message': 'Failed to retrieve charged resources'
            })
        }

def already_processed_today(execution_id):
    """Check if the report has already been processed today"""
    try:
        s3 = boto3.client('s3')
        s3.head_object(Bucket=BUCKET_NAME, Key=f"executions/{execution_id}.json")
        logger.info(f"Found existing execution record: {execution_id}")
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == '404':
            logger.info(f"No existing execution record found: {execution_id}")
            return False
        else:
            logger.warning(f"Error checking execution record: {str(e)}")
            return False
    except Exception as e:
        logger.warning(f"Unexpected error checking execution record: {str(e)}")
        return False

def mark_as_processed(execution_id, report_data):
    """Mark the execution as processed"""
    try:
        s3 = boto3.client('s3')
        
        # Create a summary for storage
        summary = {
            'execution_id': execution_id,
            'processed_at': datetime.utcnow().isoformat(),
            'total_cost': report_data.get('total_cost', 0),
            'resources_count': len(report_data.get('detailed_resources', [])),
            'processing_stats': report_data.get('processing_stats', {})
        }
        
        # Store execution record
        s3.put_object(
            Bucket=BUCKET_NAME,
            Key=f"executions/{execution_id}.json",
            Body=json.dumps(summary, indent=2, default=str),
            ContentType='application/json'
        )
        
        # Store full report
        s3.put_object(
            Bucket=BUCKET_NAME,
            Key=f"reports/{execution_id}-full-report.json",
            Body=json.dumps(report_data, indent=2, default=str),
            ContentType='application/json'
        )
        
        logger.info(f"Marked execution as processed: {execution_id}")
        
    except Exception as e:
        logger.error(f"Failed to mark execution as processed: {str(e)}")
        # Don't raise exception here to avoid breaking the main flow

def get_all_charged_resources(charged_services):
    """Get all charged resources across regions in parallel"""
    try:
        # Get all available regions
        ec2_client = boto3.client('ec2', region_name='us-east-1')
        regions = [region['RegionName'] for region in ec2_client.describe_regions()['Regions']]
        
        region_resources = {}
        
        # Process regions in parallel
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # Submit all region tasks
            future_to_region = {
                executor.submit(get_charged_resources_in_region, region, charged_services): region
                for region in regions
            }
            
            # Collect results
            for future in as_completed(future_to_region):
                region = future_to_region[future]
                try:
                    resources = future.result(timeout=30)  # 30 second timeout per region
                    if resources:
                        region_resources[region] = resources
                        logger.info(f"Found {len(resources)} resources in {region}")
                except Exception as e:
                    logger.warning(f"Error processing region {region}: {str(e)}")
        
        # Get global resources
        global_resources = get_global_charged_resources(charged_services)
        if global_resources:
            region_resources['global'] = global_resources
        
        return region_resources, global_resources
        
    except Exception as e:
        logger.error(f"Error getting charged resources: {str(e)}")
        return {}, []

def get_detailed_cost_explorer_data():
    """
    Get detailed cost data from AWS Cost Explorer for the last 30 days with usage type breakdown
    """
    try:
        ce_client = boto3.client('ce')
        
        # Get date range (last 30 days)
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=30)
        
        # Get cost by service first
        service_response = ce_client.get_cost_and_usage(
            TimePeriod={
                'Start': start_date.strftime('%Y-%m-%d'),
                'End': end_date.strftime('%Y-%m-%d')
            },
            Granularity='MONTHLY',
            Metrics=['BlendedCost'],
            GroupBy=[
                {
                    'Type': 'DIMENSION',
                    'Key': 'SERVICE'
                }
            ]
        )
        
        # Get detailed breakdown by service and usage type WITH USAGE QUANTITY
        detailed_response = ce_client.get_cost_and_usage(
            TimePeriod={
                'Start': start_date.strftime('%Y-%m-%d'),
                'End': end_date.strftime('%Y-%m-%d')
            },
            Granularity='MONTHLY',
            Metrics=['BlendedCost', 'UsageQuantity'], # Add UsageQuantity metric
            GroupBy=[
                {
                    'Type': 'DIMENSION',
                    'Key': 'SERVICE'
                },
                {
                    'Type': 'DIMENSION',
                    'Key': 'USAGE_TYPE'
                }
            ]
        )
        
        cost_by_service = {}
        detailed_breakdown = defaultdict(dict)
        total_cost = 0.0
        
        # Process service-level costs
        for result in service_response['ResultsByTime']:
            for group in result['Groups']:
                service = group['Keys'][0]
                cost = float(group['Metrics']['BlendedCost']['Amount'])
                if cost > 0:
                    cost_by_service[service] = cost
                    total_cost += cost
        
        # Process detailed breakdown
        for result in detailed_response['ResultsByTime']:
            for group in result['Groups']:
                service = group['Keys'][0]
                usage_type = group['Keys'][1]
                cost = float(group['Metrics']['BlendedCost']['Amount'])
                usage_quantity = float(group['Metrics']['UsageQuantity']['Amount'])
                
                if cost > 0:
                    # Clean up usage type names for better readability
                    clean_usage_type = clean_usage_type_name(usage_type, service)
                    detailed_breakdown[service][clean_usage_type] = {
                        'cost': cost,
                        'usage_quantity': usage_quantity,
                        'usage_type_raw': usage_type,
                        'rate_per_unit': cost / usage_quantity if usage_quantity > 0 else 0
                    }
        
        return {
            'total_cost': round(total_cost, 2),
            'by_service': cost_by_service,
            'detailed_breakdown': dict(detailed_breakdown)
        }
        
    except Exception as e:
        logger.error(f"Error getting cost explorer data: {str(e)}")
        return {'total_cost': 0.0, 'by_service': {}, 'detailed_breakdown': {}}

def get_usage_unit_for_type(usage_type, service):
    """
    Determine the appropriate unit for usage quantity based on usage type and service
    """
    usage_type_lower = usage_type.lower()
    
    # NAT Gateway
    if 'natgateway' in usage_type_lower:
        if 'hour' in usage_type_lower:
            return 'Hrs'
        elif 'byte' in usage_type_lower or 'gb' in usage_type_lower:
            return 'GB'
    
    # EC2 Instances
    elif 'boxusage' in usage_type_lower or 'instanceusage' in usage_type_lower:
        return 'Hrs'
    
    # EBS
    elif 'volumeusage' in usage_type_lower:
        return 'GB-Mo'
    elif 'snapshotusage' in usage_type_lower:
        return 'GB-Mo'
    elif 'iops' in usage_type_lower:
        return 'IOPS-Mo'
    
    # RDS
    elif 'db' in usage_type_lower and 'instanceusage' in usage_type_lower:
        return 'Hrs'
    elif 'rds' in usage_type_lower and 'storageusage' in usage_type_lower:
        return 'GB-Mo'
    
    # S3
    elif 'storageusage' in usage_type_lower and 's3' in service.lower():
        return 'GB-Mo'
    elif 'request' in usage_type_lower and 's3' in service.lower():
        return 'Requests'
    
    # Lambda
    elif 'request' in usage_type_lower and 'lambda' in service.lower():
        return 'Requests'
    elif 'duration' in usage_type_lower and 'lambda' in service.lower():
        return 'GB-Seconds'
    
    # Data Transfer
    elif 'datatransfer' in usage_type_lower:
        return 'GB'
    
    # Load Balancer
    elif 'loadbalancer' in usage_type_lower:
        return 'Hrs'
    
    # CloudFront
    elif 'datatransfer' in usage_type_lower and 'cloudfront' in service.lower():
        return 'GB'
    elif 'request' in usage_type_lower and 'cloudfront' in service.lower():
        return 'Requests'
    
    # Default
    return 'Units'

def clean_usage_type_name(usage_type, service):
    """
    Clean up usage type names to make them more readable
    """
    # Remove region prefixes
    cleaned = usage_type
    
    # Common patterns to clean up
    region_prefixes = ['USE1-', 'USE2-', 'USW1-', 'USW2-', 'EUW1-', 'EUW2-', 'EUW3-', 
                      'APS1-', 'APS2-', 'APN1-', 'APN2-', 'SAE1-', 'CAN1-', 'EUC1-']
    
    for prefix in region_prefixes:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
            break
    
    # Service-specific cleaning
    if 'Amazon Virtual Private Cloud' in service or 'VPC' in service:
        if 'NatGateway' in cleaned:
            return 'NAT Gateway Hours'
        elif 'PublicIP' in cleaned:
            return 'Elastic IP Addresses'
        elif 'VpcEndpoint' in cleaned:
            return 'VPC Endpoints'
        elif 'VPN' in cleaned:
            return 'VPN Connection Hours'
    
    elif 'Amazon Elastic Compute Cloud' in service or 'EC2' in service:
        if 'BoxUsage' in cleaned:
            # Extract instance type
            parts = cleaned.split(':')
            if len(parts) > 1:
                return f"EC2 Instance - {parts[1]}"
            return 'EC2 Instance Hours'
        elif 'EBS' in cleaned:
            if 'VolumeUsage' in cleaned:
                return 'EBS Volume Storage'
            elif 'SnapshotUsage' in cleaned:
                return 'EBS Snapshot Storage'
            elif 'IOPS' in cleaned:
                return 'EBS Provisioned IOPS'
        elif 'DataTransfer' in cleaned:
            return 'Data Transfer'
        elif 'LoadBalancer' in cleaned:
            return 'Load Balancer Hours'
    
    elif 'Amazon Relational Database Service' in service:
        if 'InstanceUsage' in cleaned:
            parts = cleaned.split(':')
            if len(parts) > 1:
                return f"RDS Instance - {parts[1]}"
            return 'RDS Instance Hours'
        elif 'StorageUsage' in cleaned:
            return 'RDS Storage'
        elif 'BackupUsage' in cleaned:
            return 'RDS Backup Storage'
        elif 'IOPS' in cleaned:
            return 'RDS Provisioned IOPS'
    
    elif 'Amazon Simple Storage Service' in service or 'S3' in service:
        if 'StorageUsage' in cleaned:
            return 'S3 Storage'
        elif 'Requests' in cleaned:
            return 'S3 Requests'
        elif 'DataTransfer' in cleaned:
            return 'S3 Data Transfer'
    
    elif 'AWS Lambda' in service:
        if 'Request' in cleaned:
            return 'Lambda Requests'
        elif 'Duration' in cleaned:
            return 'Lambda Duration'
    
    elif 'Amazon ElastiCache' in service:
        if 'NodeUsage' in cleaned:
            return 'ElastiCache Node Hours'
        elif 'BackupUsage' in cleaned:
            return 'ElastiCache Backup Storage'
    
    elif 'Amazon CloudFront' in service:
        if 'DataTransfer' in cleaned:
            return 'CloudFront Data Transfer'
        elif 'Request' in cleaned:
            return 'CloudFront Requests'
    
    # Return original if no specific pattern matched
    return cleaned

def get_charged_resources_in_region(region, charged_services):
    """
    Get charged resources in a specific region
    """
    resources = []
    
    try:
        # EC2 Instances
        if any('Elastic Compute Cloud' in service or 'EC2' in service for service in charged_services):
            resources.extend(get_ec2_instances(region))
        
        # RDS Instances
        if any('Relational Database Service' in service or 'RDS' in service for service in charged_services):
            resources.extend(get_rds_instances(region))
        
        # EBS Volumes
        if any('Elastic Block Store' in service or 'EBS' in service for service in charged_services):
            resources.extend(get_ebs_volumes(region))
        
        # Load Balancers
        if any('Elastic Load Balancing' in service or 'ELB' in service for service in charged_services):
            resources.extend(get_load_balancers(region))
        
        # NAT Gateways and VPC resources
        if any('Virtual Private Cloud' in service or 'VPC' in service for service in charged_services):
            resources.extend(get_nat_gateways(region))
            resources.extend(get_elastic_ips(region))
            resources.extend(get_vpc_endpoints(region))
        
        # ElastiCache
        if any('ElastiCache' in service for service in charged_services):
            resources.extend(get_elasticache_clusters(region))
        
        # Redshift
        if any('Redshift' in service for service in charged_services):
            resources.extend(get_redshift_clusters(region))
        
        # Lambda
        if any('Lambda' in service for service in charged_services):
            resources.extend(get_lambda_functions(region))
        
    except Exception as e:
        logger.error(f"Error getting resources in region {region}: {str(e)}")
    
    return resources

def get_elastic_ips(region):
    """Get Elastic IP addresses"""
    try:
        ec2 = boto3.client('ec2', region_name=region)
        response = ec2.describe_addresses()
        
        eips = []
        for eip in response['Addresses']:
            eips.append({
                'service': 'VPC',
                'resource_type': 'Elastic IP',
                'resource_id': eip.get('AllocationId', eip.get('PublicIp', 'N/A')),
                'region': region,
                'state': 'associated' if eip.get('AssociationId') else 'unassociated',
                'public_ip': eip.get('PublicIp', 'N/A'),
                'instance_id': eip.get('InstanceId', 'N/A')
            })
        
        return eips
    except Exception as e:
        logger.error(f"Error getting Elastic IPs in {region}: {str(e)}")
        return []

def get_vpc_endpoints(region):
    """Get VPC Endpoints"""
    try:
        ec2 = boto3.client('ec2', region_name=region)
        response = ec2.describe_vpc_endpoints()
        
        endpoints = []
        for endpoint in response['VpcEndpoints']:
            endpoints.append({
                'service': 'VPC',
                'resource_type': 'VPC Endpoint',
                'resource_id': endpoint['VpcEndpointId'],
                'region': region,
                'state': endpoint['State'],
                'service_name': endpoint['ServiceName'],
                'vpc_id': endpoint['VpcId']
            })
        
        return endpoints
    except Exception as e:
        logger.error(f"Error getting VPC Endpoints in {region}: {str(e)}")
        return []

def send_email_report(charged_resources):
    """
    Send email report using Amazon SES
    """
    try:
        if not RECIPIENT_EMAILS or not RECIPIENT_EMAILS[0]:
            logger.warning("No recipient emails configured")
            return False
        
        ses_client = boto3.client('ses', region_name=AWS_REGION_SES)
        
        # Generate email content
        subject = f"AWS Detailed Cost Report - {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}"
        html_body = generate_html_email_body(charged_resources)
        text_body = generate_text_email_body(charged_resources)
        
        # Send email to each recipient
        for recipient in RECIPIENT_EMAILS:
            recipient = recipient.strip()
            if not recipient:
                continue
                
            response = ses_client.send_email(
                Source=SENDER_EMAIL,
                Destination={'ToAddresses': [recipient]},
                Message={
                    'Subject': {'Data': subject, 'Charset': 'UTF-8'},
                    'Body': {
                        'Html': {'Data': html_body, 'Charset': 'UTF-8'},
                        'Text': {'Data': text_body, 'Charset': 'UTF-8'}
                    }
                }
            )
            logger.info(f"Email sent successfully to {recipient}: {response['MessageId']}")
        
        return True
        
    except Exception as e:
        logger.error(f"Error sending email: {str(e)}")
        return False

def generate_html_email_body(charged_resources):
    """
    Generate HTML email body with detailed cost breakdown
    """
    total_cost = charged_resources.get('total_cost', 0)
    timestamp = charged_resources.get('timestamp', '')
    resources_by_service = charged_resources.get('resources_by_service', {})
    detailed_breakdown = charged_resources.get('detailed_cost_breakdown', {})
    resources_by_region = charged_resources.get('resources_by_region', {})
    detailed_resources = charged_resources.get('detailed_resources', [])
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; }}
            .header {{ background-color: #f8f9fa; padding: 20px; border-radius: 5px; margin-bottom: 20px; }}
            .cost-summary {{ background-color: #e8f5e9; padding: 15px; border-radius: 5px; margin-bottom: 20px; }}
            .warning {{ background-color: #fff3cd; padding: 15px; border-radius: 5px; margin-bottom: 20px; }}
            table {{ border-collapse: collapse; width: 100%; margin-bottom: 20px; }}
            th, td {{ border: 1px solid #ddd; padding: 12px; text-align: left; }}
            th {{ background-color: #f2f2f2; font-weight: bold; }}
            .service-header {{ background-color: #e3f2fd; font-weight: bold; }}
            .usage-type-row {{ background-color: #f8f9fa; }}
            .region-header {{ background-color: #f3e5f5; font-weight: bold; }}
            .cost-high {{ color: #d32f2f; font-weight: bold; }}
            .cost-medium {{ color: #f57c00; font-weight: bold; }}
            .cost-low {{ color: #388e3c; }}
            .indent {{ padding-left: 20px; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>💰 AWS Detailed Cost & Resource Report</h1>
            <p><strong>Generated:</strong> {timestamp}</p>
            <p><strong>Scan Period:</strong> Last 30 days</p>
        </div>
        
        <div class="cost-summary">
            <h2>💰 Total Cost Summary</h2>
            <p><strong>Total Cost (Last 30 days):</strong> 
                <span class="{'cost-high' if total_cost > 100 else 'cost-medium' if total_cost > 10 else 'cost-low'}">
                    ${total_cost:.2f}
                </span>
            </p>
        </div>
    """
    
    # Detailed Cost Breakdown by Service and Usage Type
    if detailed_breakdown:
        html += """
        <h2>📊 Detailed Cost Breakdown by Service & Resource Type</h2>
        <table>
            <tr>
                <th>Service / Resource Type</th>
                <th>Cost (Last 30 days)</th>
                <th>Usage Details</th>
                <th>Percentage of Total</th>
            </tr>
        """
        
        sorted_services = sorted(resources_by_service.items(), key=lambda x: x[1], reverse=True)
        for service, service_cost in sorted_services:
            service_percentage = (service_cost / total_cost * 100) if total_cost > 0 else 0
            service_cost_class = 'cost-high' if service_cost > 50 else 'cost-medium' if service_cost > 5 else 'cost-low'
            
            html += f"""
            <tr class="service-header">
                <td><strong>{service}</strong></td>
                <td class="{service_cost_class}"><strong>${service_cost:.2f}</strong></td>
                <td><strong>Service Total</strong></td>
                <td><strong>{service_percentage:.1f}%</strong></td>
            </tr>
            """
            
             # Add detailed breakdown for this service WITH USAGE DATA
            if service in detailed_breakdown:
                usage_items = sorted(detailed_breakdown[service].items(), key=lambda x: x[1]['cost'], reverse=True)
                for usage_type, usage_data in usage_items:
                    usage_cost = usage_data['cost']
                    if usage_cost > 0.01:  # Only show costs > $0.01
                        usage_percentage = (usage_cost / service_cost * 100) if service_cost > 0 else 0
                        usage_cost_class = 'cost-high' if usage_cost > 20 else 'cost-medium' if usage_cost > 2 else 'cost-low'
                        
                        # Format usage details
                        usage_quantity = usage_data['usage_quantity']
                        rate_per_unit = usage_data['rate_per_unit']
                        usage_type_raw = usage_data['usage_type_raw']
                        unit = get_usage_unit_for_type(usage_type_raw, service)
                        
                        # Create usage details string
                        usage_details = ""
                        if usage_quantity > 0 and rate_per_unit > 0:
                            if usage_quantity >= 1000:
                                formatted_quantity = f"{usage_quantity:,.0f}"
                            elif usage_quantity >= 1:
                                formatted_quantity = f"{usage_quantity:.1f}"
                            else:
                                formatted_quantity = f"{usage_quantity:.3f}"
                            
                            usage_details = f"${rate_per_unit:.3f} per {unit} × {formatted_quantity} {unit}"
                        
                        html += f"""
                        <tr class="usage-type-row">
                            <td class="indent">├─ {usage_type}</td>
                            <td class="{usage_cost_class}">${usage_cost:.2f}</td>
                            <td class="usage-details">{usage_details}</td>
                            <td>{usage_percentage:.1f}% of service</td>
                        </tr>
                        """
        html += "</table>"
    
    # Resources by Region (existing code remains the same)
    if resources_by_region:
        html += "<h2>🌍 Resources by Region</h2><table>"
        html += "<tr><th>Region</th><th>Service</th><th>Resource Type</th><th>Resource ID</th><th>State</th><th>Details</th></tr>"
        
        for region, resources in resources_by_region.items():
            region_resource_count = len(resources)
            html += f'<tr class="region-header"><td colspan="6">{region.upper()} ({region_resource_count} resources)</td></tr>'
            
            # Group by service
            service_groups = defaultdict(list)
            for resource in resources:
                service_groups[resource['service']].append(resource)
            
            for service, service_resources in service_groups.items():
                html += f'<tr class="service-header"><td></td><td colspan="5">{service} ({len(service_resources)} resources)</td></tr>'
                
                for resource in service_resources:
                    details = []
                    for key, value in resource.items():
                        if key not in ['service', 'resource_type', 'resource_id', 'region', 'state']:
                            details.append(f"{key}: {value}")
                    
                    html += f"""
                    <tr>
                        <td></td>
                        <td></td>
                        <td>{resource.get('resource_type', 'N/A')}</td>
                        <td>{resource.get('resource_id', 'N/A')}</td>
                        <td>{resource.get('state', 'N/A')}</td>
                        <td>{', '.join(details[:3])}{'...' if len(details) > 3 else ''}</td>
                    </tr>
                    """
        html += "</table>"
    
    # Summary
    total_resources = len(detailed_resources)
    html += f"""
        <div class="cost-summary">
            <h2>📈 Summary</h2>
            <ul>
                <li><strong>Total Resources Found:</strong> {total_resources}</li>
                <li><strong>Regions Scanned:</strong> {len(resources_by_region)}</li>
                <li><strong>Services with Charges:</strong> {len(resources_by_service)}</li>
                <li><strong>Detailed Usage Types:</strong> {sum(len(breakdown) for breakdown in detailed_breakdown.values())}</li>
            </ul>
        </div>
        
        <hr>
        <p><small>This detailed report was generated automatically by AWS Lambda. 
        For even more granular cost analysis, please check your AWS Cost Explorer dashboard.</small></p>
    </body>
    </html>
    """
    
    return html

def generate_text_email_body(charged_resources):
    """
    Generate plain text email body with detailed breakdown
    """
    total_cost = charged_resources.get('total_cost', 0)
    timestamp = charged_resources.get('timestamp', '')
    resources_by_service = charged_resources.get('resources_by_service', {})
    detailed_breakdown = charged_resources.get('detailed_cost_breakdown', {})
    resources_by_region = charged_resources.get('resources_by_region', {})
    detailed_resources = charged_resources.get('detailed_resources', [])
    
    text = f"""
AWS DETAILED COST & RESOURCE REPORT
Generated: {timestamp}
Scan Period: Last 30 days

TOTAL COST SUMMARY
Total Cost (Last 30 days): ${total_cost:.2f}

DETAILED COST BREAKDOWN BY SERVICE & RESOURCE TYPE
{'=' * 60}
"""
    
    # Detailed Cost Breakdown
    if detailed_breakdown:
        sorted_services = sorted(resources_by_service.items(), key=lambda x: x[1], reverse=True)
        for service, service_cost in sorted_services:
            service_percentage = (service_cost / total_cost * 100) if total_cost > 0 else 0
            text += f"\n{service}: ${service_cost:.2f} ({service_percentage:.1f}%)\n"
            text += "-" * 50 + "\n"
            
            if service in detailed_breakdown:
                usage_types = sorted(detailed_breakdown[service].items(), key=lambda x: x[1]['cost'], reverse=True)
                for usage_type, usage_data in usage_types:
                    usage_cost = usage_data['cost']
                    if usage_cost > 0.01:
                        usage_percentage = (usage_cost / service_cost * 100) if service_cost > 0 else 0
                        text += f"  ├─ {usage_type}: ${usage_cost:.2f} ({usage_percentage:.1f}% of service)\n"
            text += "\n"
    
    # Resources by Region (simplified for text)
    if resources_by_region:
        text += "RESOURCES BY REGION\n"
        text += "=" * 40 + "\n"
        
        for region, resources in resources_by_region.items():
            text += f"\n{region.upper()} ({len(resources)} resources)\n"
            
            # Group by service
            service_groups = defaultdict(list)
            for resource in resources:
                service_groups[resource['service']].append(resource)
            
            for service, service_resources in service_groups.items():
                text += f"  {service}: {len(service_resources)} resources\n"
                for resource in service_resources[:3]:  # Limit to first 3 per service
                    text += f"    - {resource.get('resource_type', 'N/A')}: {resource.get('resource_id', 'N/A')} ({resource.get('state', 'N/A')})\n"
                if len(service_resources) > 3:
                    text += f"    ... and {len(service_resources) - 3} more\n"
    
    # Summary
    total_resources = len(detailed_resources)
    text += f"""

SUMMARY
{'=' * 40}
Total Resources Found: {total_resources}
Regions Scanned: {len(resources_by_region)}
Services with Charges: {len(resources_by_service)}
Detailed Usage Types: {sum(len(breakdown) for breakdown in detailed_breakdown.values())}

This detailed report was generated automatically by AWS Lambda.
For even more granular cost analysis, please check your AWS Cost Explorer dashboard.
"""
    
    return text

# Keep all the existing resource gathering functions unchanged
def get_ec2_instances(region):
    """Get running EC2 instances"""
    try:
        ec2 = boto3.client('ec2', region_name=region)
        response = ec2.describe_instances(
            Filters=[{'Name': 'instance-state-name', 'Values': ['running', 'stopped']}]
        )
        
        instances = []
        for reservation in response['Reservations']:
            for instance in reservation['Instances']:
                instances.append({
                    'service': 'EC2',
                    'resource_type': 'Instance',
                    'resource_id': instance['InstanceId'],
                    'region': region,
                    'state': instance['State']['Name'],
                    'instance_type': instance['InstanceType'],
                    'launch_time': instance['LaunchTime']
                })
        
        return instances
    except Exception as e:
        logger.error(f"Error getting EC2 instances in {region}: {str(e)}")
        return []

def get_rds_instances(region):
    """Get RDS instances"""
    try:
        rds = boto3.client('rds', region_name=region)
        response = rds.describe_db_instances()
        
        instances = []
        for db in response['DBInstances']:
            instances.append({
                'service': 'RDS',
                'resource_type': 'DB Instance',
                'resource_id': db['DBInstanceIdentifier'],
                'region': region,
                'state': db['DBInstanceStatus'],
                'instance_class': db['DBInstanceClass'],
                'engine': db['Engine']
            })
        
        return instances
    except Exception as e:
        logger.error(f"Error getting RDS instances in {region}: {str(e)}")
        return []

def get_ebs_volumes(region):
    """Get EBS volumes"""
    try:
        ec2 = boto3.client('ec2', region_name=region)
        response = ec2.describe_volumes()
        
        volumes = []
        for volume in response['Volumes']:
            volumes.append({
                'service': 'EBS',
                'resource_type': 'Volume',
                'resource_id': volume['VolumeId'],
                'region': region,
                'state': volume['State'],
                'size_gb': volume['Size'],
                'volume_type': volume['VolumeType']
            })
        
        return volumes
    except Exception as e:
        logger.error(f"Error getting EBS volumes in {region}: {str(e)}")
        return []

def get_load_balancers(region):
    """Get Load Balancers (ALB/NLB/CLB)"""
    resources = []
    
    try:
        # Application and Network Load Balancers
        elbv2 = boto3.client('elbv2', region_name=region)
        response = elbv2.describe_load_balancers()
        
        for lb in response['LoadBalancers']:
            resources.append({
                'service': 'ELB',
                'resource_type': 'Load Balancer',
                'resource_id': lb['LoadBalancerName'],
                'region': region,
                'state': lb['State']['Code'],
                'type': lb['Type'],
                'scheme': lb['Scheme']
            })
    except Exception as e:
        logger.error(f"Error getting ALB/NLB in {region}: {str(e)}")
    
    try:
        # Classic Load Balancers
        elb = boto3.client('elb', region_name=region)
        response = elb.describe_load_balancers()
        
        for lb in response['LoadBalancerDescriptions']:
            resources.append({
                'service': 'ELB',
                'resource_type': 'Classic Load Balancer',
                'resource_id': lb['LoadBalancerName'],
                'region': region,
                'scheme': lb['Scheme']
            })
    except Exception as e:
        logger.error(f"Error getting Classic ELB in {region}: {str(e)}")
    
    return resources

def get_nat_gateways(region):
    """Get NAT Gateways"""
    try:
        ec2 = boto3.client('ec2', region_name=region)
        response = ec2.describe_nat_gateways()
        
        gateways = []
        for nat in response['NatGateways']:
            if nat['State'] in ['available', 'pending']:
                gateways.append({
                    'service': 'VPC',
                    'resource_type': 'NAT Gateway',
                    'resource_id': nat['NatGatewayId'],
                    'region': region,
                    'state': nat['State'],
                    'subnet_id': nat['SubnetId']
                })
        
        return gateways
    except Exception as e:
        logger.error(f"Error getting NAT Gateways in {region}: {str(e)}")
        return []

def get_elasticache_clusters(region):
    """Get ElastiCache clusters"""
    try:
        elasticache = boto3.client('elasticache', region_name=region)
        response = elasticache.describe_cache_clusters()
        
        clusters = []
        for cluster in response['CacheClusters']:
            clusters.append({
                'service': 'ElastiCache',
                'resource_type': 'Cache Cluster',
                'resource_id': cluster['CacheClusterId'],
                'region': region,
                'state': cluster['CacheClusterStatus'],
                'node_type': cluster['CacheNodeType'],
                'engine': cluster['Engine']
            })
        
        return clusters
    except Exception as e:
        logger.error(f"Error getting ElastiCache clusters in {region}: {str(e)}")
        return []

def get_redshift_clusters(region):
    """Get Redshift clusters"""
    try:
        redshift = boto3.client('redshift', region_name=region)
        response = redshift.describe_clusters()
        
        clusters = []
        for cluster in response['Clusters']:
            clusters.append({
                'service': 'Redshift',
                'resource_type': 'Cluster',
                'resource_id': cluster['ClusterIdentifier'],
                'region': region,
                'state': cluster['ClusterStatus'],
                'node_type': cluster['NodeType'],
                'number_of_nodes': cluster['NumberOfNodes']
            })
        
        return clusters
    except Exception as e:
        logger.error(f"Error getting Redshift clusters in {region}: {str(e)}")
        return []

def get_lambda_functions(region):
    """Get Lambda functions (only if they have recent invocations)"""
    try:
        lambda_client = boto3.client('lambda', region_name=region)
        response = lambda_client.list_functions()
        
        functions = []
        for func in response['Functions']:
            # Only include functions that might be generating charges
            functions.append({
                'service': 'Lambda',
                'resource_type': 'Function',
                'resource_id': func['FunctionName'],
                'region': region,
                'runtime': func['Runtime'],
                'memory_size': func['MemorySize'],
                'last_modified': func['LastModified']
            })
        
        return functions
    except Exception as e:
        logger.error(f"Error getting Lambda functions in {region}: {str(e)}")
        return []

def get_global_charged_resources(charged_services):
    """Get global resources that might be charged"""
    resources = []
    
    try:
        # CloudFront distributions
        if any('CloudFront' in service for service in charged_services):
            cloudfront = boto3.client('cloudfront')
            response = cloudfront.list_distributions()
            
            if 'DistributionList' in response and 'Items' in response['DistributionList']:
                for dist in response['DistributionList']['Items']:
                    resources.append({
                        'service': 'CloudFront',
                        'resource_type': 'Distribution',
                        'resource_id': dist['Id'],
                        'region': 'global',
                        'state': dist['Status'],
                        'domain_name': dist['DomainName']
                    })
        
        # Route 53 hosted zones
        if any('Route 53' in service for service in charged_services):
            route53 = boto3.client('route53')
            response = route53.list_hosted_zones()
            
            for zone in response['HostedZones']:
                resources.append({
                    'service': 'Route53',
                    'resource_type': 'Hosted Zone',
                    'resource_id': zone['Id'],
                    'region': 'global',
                    'name': zone['Name'],
                    'record_count': zone['ResourceRecordSetCount']
                })
        
        # WAF Web ACLs
        if any('WAF' in service for service in charged_services):
            try:
                waf = boto3.client('wafv2', region_name='us-east-1')  # WAFv2 is global but accessed via us-east-1
                response = waf.list_web_acls(Scope='REGIONAL')
                
                for acl in response['WebACLs']:
                    resources.append({
                        'service': 'WAF',
                        'resource_type': 'Web ACL',
                        'resource_id': acl['Name'],
                        'region': 'global',
                        'state': 'active',
                        'arn': acl['ARN']
                    })
            except Exception as e:
                logger.error(f"Error getting WAF resources: {str(e)}")
    
    except Exception as e:
        logger.error(f"Error getting global resources: {str(e)}")
    
    return resources