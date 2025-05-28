#!/bin/bash
# scripts/deploy.sh

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${GREEN}🚀 AWS Cost Reporter Management Script${NC}"

# Show usage
show_usage() {
    echo -e "${BLUE}Usage: $0 [deploy|destroy]${NC}"
    echo -e "${BLUE}  deploy  - Deploy/update the infrastructure${NC}"
    echo -e "${BLUE}  destroy - Destroy all resources${NC}"
    echo -e "${BLUE}  No argument - Interactive mode${NC}"
}

# Check if required tools are installed
check_requirements() {
    echo -e "${YELLOW}📋 Checking requirements...${NC}"
    
    if ! command -v terraform &> /dev/null; then
        echo -e "${RED}❌ Terraform is not installed${NC}"
        exit 1
    fi
    
    if ! command -v aws &> /dev/null; then
        echo -e "${RED}❌ AWS CLI is not installed${NC}"
        exit 1
    fi
    
    echo -e "${GREEN}✅ All requirements met${NC}"
}

# Check AWS credentials
check_aws_credentials() {
    echo -e "${YELLOW}🔐 Checking AWS credentials...${NC}"
    
    if ! aws sts get-caller-identity &> /dev/null; then
        echo -e "${RED}❌ AWS credentials not configured${NC}"
        echo -e "${YELLOW}Run: aws configure${NC}"
        exit 1
    fi
    
    echo -e "${GREEN}✅ AWS credentials configured${NC}"
}

# Setup Terraform files
setup_terraform() {
    echo -e "${YELLOW}📁 Setting up Terraform files...${NC}"
    
    # Create directory structure
    mkdir -p terraform/scripts
    
    # Copy Lambda function
    if [ ! -f "sources/lambda_function.py" ]; then
        echo -e "${YELLOW}⚠️  lambda_function.py not found. Please copy the Lambda function code to sources/lambda_function.py${NC}"
        read -p "Press Enter after copying the file..."
    fi
    
    # Check if terraform.tfvars exists
    if [ ! -f "terraform/terraform.tfvars" ]; then
        echo -e "${YELLOW}⚠️  terraform.tfvars not found${NC}"
        echo -e "${YELLOW}Please create terraform.tfvars based on terraform.tfvars.example${NC}"
        read -p "Press Enter after creating the file..."
    fi
    
    echo -e "${GREEN}✅ Terraform files ready${NC}"
}

# Verify SES setup
verify_ses() {
    echo -e "${YELLOW}📧 Checking SES setup...${NC}"
    
    # Get sender email from terraform.tfvars
    SENDER_EMAIL=$(grep 'sender_email' terraform/terraform.tfvars | cut -d'"' -f2)
    
    if [ -z "$SENDER_EMAIL" ]; then
        echo -e "${RED}❌ Sender email not found in terraform.tfvars${NC}"
        exit 1
    fi
    
    echo -e "${YELLOW}📧 Sender email: $SENDER_EMAIL${NC}"
    echo -e "${YELLOW}⚠️  Make sure to verify this email in AWS SES console${NC}"
    echo -e "${YELLOW}⚠️  Also verify recipient emails if you're in SES sandbox mode${NC}"
    
    read -p "Have you verified the email addresses in SES? (y/N): " -r
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}Please verify email addresses in AWS SES console first${NC}"
        echo -e "${YELLOW}AWS Console -> SES -> Verified identities${NC}"
        exit 1
    fi
    
    echo -e "${GREEN}✅ SES setup verified${NC}"
}

# Initialize Terraform
init_terraform() {
    cd terraform || exit 1
    
    # Check if already initialized
    if [ -d ".terraform" ]; then
        echo -e "${YELLOW}🔁 Terraform already initialized, skipping init...${NC}"
    else
        echo -e "${YELLOW}📦 Initializing Terraform...${NC}"
        terraform init
    fi
}

# Deploy with Terraform
deploy() {
    echo -e "${YELLOW}🏗️  Deploying infrastructure...${NC}"
    
    init_terraform
    
    # Plan deployment
    echo -e "${YELLOW}📋 Planning deployment...${NC}"
    terraform plan -out=tfplan
    
    # Ask for confirmation
    echo -e "${YELLOW}🤔 Review the plan above${NC}"
    read -p "Do you want to proceed with deployment? (y/N): " -r
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}Deployment cancelled${NC}"
        cd ..
        exit 0
    fi
    
    # Apply deployment
    echo -e "${YELLOW}🚀 Applying deployment...${NC}"
    terraform apply tfplan
    
    echo -e "${GREEN}✅ Deployment completed successfully!${NC}"
    
    # Show outputs
    echo -e "${YELLOW}📊 Deployment information:${NC}"
    terraform output
    
    cd ..
}

# Destroy infrastructure
destroy() {
    echo -e "${RED}💥 Destroying infrastructure...${NC}"
    
    init_terraform
    
    # Plan destroy
    echo -e "${YELLOW}📋 Planning destruction...${NC}"
    terraform plan -destroy
    
    # Double confirmation for destroy
    echo -e "${RED}⚠️  WARNING: This will destroy ALL resources!${NC}"
    echo -e "${RED}This action cannot be undone!${NC}"
    read -p "Are you absolutely sure you want to destroy all resources? (type 'yes'): " -r
    if [[ $REPLY != "yes" ]]; then
        echo -e "${YELLOW}Destruction cancelled${NC}"
        cd ..
        exit 0
    fi
    
    # Apply destroy
    echo -e "${RED}💥 Destroying resources...${NC}"
    terraform destroy -auto-approve
    
    echo -e "${GREEN}✅ Resources destroyed successfully!${NC}"
    
    cd ..
}

# Test the Lambda function
test_function() {
    echo -e "${YELLOW}🧪 Testing Lambda function...${NC}"
    
    cd terraform || exit 1
    
    # Check if infrastructure exists
    if [ ! -f "terraform.tfstate" ] || [ ! -s "terraform.tfstate" ]; then
        echo -e "${RED}❌ No infrastructure found. Please deploy first.${NC}"
        cd ..
        return 1
    fi
    
    FUNCTION_NAME=$(terraform output -raw lambda_function_arn 2>/dev/null)
    
    if [ -z "$FUNCTION_NAME" ]; then
        echo -e "${RED}❌ Lambda function not found. Please deploy first.${NC}"
        cd ..
        return 1
    fi
    
    echo -e "${YELLOW}📞 Invoking Lambda function: $FUNCTION_NAME${NC}"
    
    aws lambda invoke \
        --function-name "$FUNCTION_NAME" \
        --region ap-southeast-2 \
        --payload '{"test": true}' \
        --cli-binary-format raw-in-base64-out \
        response.json
    
    echo -e "${YELLOW}📄 Response:${NC}"
    if command -v jq &> /dev/null; then
        cat response.json | jq .
    else
        cat response.json
    fi
    rm response.json
    
    echo -e "${GREEN}✅ Function test completed${NC}"
    
    cd ..
}

# Interactive mode - let user choose action
interactive_mode() {
    echo -e "${BLUE}🔧 What would you like to do?${NC}"
    echo -e "${GREEN}1) Deploy/Update infrastructure${NC}"
    echo -e "${RED}2) Destroy all resources${NC}"
    echo -e "${YELLOW}3) Test Lambda function${NC}"
    echo -e "${BLUE}4) Show infrastructure status${NC}"
    echo -e "${NC}5) Exit${NC}"
    
    read -p "Please select an option (1-5): " -r choice
    
    case $choice in
        1)
            ACTION="deploy"
            ;;
        2)
            ACTION="destroy"
            ;;
        3)
            ACTION="test"
            ;;
        4)
            ACTION="status"
            ;;
        5)
            echo -e "${YELLOW}Goodbye!${NC}"
            exit 0
            ;;
        *)
            echo -e "${RED}❌ Invalid option${NC}"
            exit 1
            ;;
    esac
}

# Show infrastructure status
show_status() {
    echo -e "${YELLOW}📊 Checking infrastructure status...${NC}"
    
    cd terraform || exit 1
    
    if [ ! -f "terraform.tfstate" ] || [ ! -s "terraform.tfstate" ]; then
        echo -e "${RED}❌ No infrastructure found${NC}"
        cd ..
        return 1
    fi
    
    echo -e "${GREEN}✅ Infrastructure exists${NC}"
    echo -e "${YELLOW}📋 Current outputs:${NC}"
    terraform output
    
    cd ..
}

# Main execution
main() {
    # Parse command line arguments
    ACTION="$1"
    
    if [ "$ACTION" == "--help" ] || [ "$ACTION" == "-h" ]; then
        show_usage
        exit 0
    fi
    
    # If no argument provided, use interactive mode
    if [ -z "$ACTION" ]; then
        interactive_mode
    fi
    
    # Basic checks
    check_requirements
    check_aws_credentials
    
    case $ACTION in
        "deploy")
            setup_terraform
            verify_ses
            deploy
            
            read -p "Do you want to test the Lambda function now? (y/N): " -r
            if [[ $REPLY =~ ^[Yy]$ ]]; then
                test_function
            fi
            
            echo -e "${GREEN}🎉 AWS Cost Reporter deployed successfully!${NC}"
            echo -e "${YELLOW}📅 The function will run daily at 9:00 AM Vietnamese time${NC}"
            echo -e "${YELLOW}📊 Check CloudWatch Logs for execution details${NC}"
            ;;
        "destroy")
            destroy
            echo -e "${GREEN}🎉 AWS Cost Reporter resources destroyed!${NC}"
            ;;
        "test")
            test_function
            ;;
        "status")
            show_status
            ;;
        *)
            echo -e "${RED}❌ Invalid action: $ACTION${NC}"
            show_usage
            exit 1
            ;;
    esac
}

# Run main function with all arguments
main "$@"