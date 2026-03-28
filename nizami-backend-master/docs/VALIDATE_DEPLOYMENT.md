# Nizami — Deployment Validation Checklist

Run each command to verify the resource exists and is in the expected state.
A command that returns output = step is done. An error or empty result = step is missing.

Set your account ID once before starting:
```bash
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export AWS_REGION=me-south-1
```

---

## DEPLOY_BABY_STEPS.md Validation

---

### Step 2 — ECR Repository

```bash
aws ecr describe-repositories \
  --repository-names nizami/backend \
  --region $AWS_REGION \
  --query 'repositories[0].repositoryUri' \
  --output text
```
**Expected:** `123456789.dkr.ecr.me-south-1.amazonaws.com/nizami/backend`

```bash
# Verify at least one image has been pushed
aws ecr describe-images \
  --repository-name nizami/backend \
  --region $AWS_REGION \
  --query 'imageDetails[0].imagePushedAt' \
  --output text
```
**Expected:** a date/time (not `None`)

---

### Step 3 — VPC

```bash
aws ec2 describe-vpcs \
  --filters "Name=tag:Name,Values=nizami-vpc" \
  --query 'Vpcs[0].{VpcId:VpcId,State:State,CidrBlock:CidrBlock}' \
  --output table
```
**Expected:** VpcId present, State = `available`, CidrBlock = `10.0.0.0/16`

```bash
# DNS support and hostnames must both be enabled
VPC_ID=$(aws ec2 describe-vpcs --filters "Name=tag:Name,Values=nizami-vpc" --query 'Vpcs[0].VpcId' --output text)
aws ec2 describe-vpc-attribute --vpc-id $VPC_ID --attribute enableDnsSupport --query 'EnableDnsSupport.Value'
aws ec2 describe-vpc-attribute --vpc-id $VPC_ID --attribute enableDnsHostnames --query 'EnableDnsHostnames.Value'
```
**Expected:** both return `true`

---

### Step 4 — Subnets (4 total: 2 public, 2 private)

```bash
aws ec2 describe-subnets \
  --filters "Name=tag:Name,Values=nizami-public-a,nizami-public-b,nizami-private-a,nizami-private-b" \
  --query 'Subnets[*].{Name:Tags[?Key==`Name`]|[0].Value,SubnetId:SubnetId,AZ:AvailabilityZone,CIDR:CidrBlock}' \
  --output table
```
**Expected:** 4 rows — one per subnet name

---

### Step 5 — Internet Gateway

```bash
aws ec2 describe-internet-gateways \
  --filters "Name=tag:Name,Values=nizami-igw" \
  --query 'InternetGateways[0].{IGW:InternetGatewayId,State:Attachments[0].State}' \
  --output table
```
**Expected:** IGW ID present, State = `available`

---

### Step 6 — NAT Gateway

```bash
aws ec2 describe-nat-gateways \
  --filter "Name=tag:Name,Values=nizami-nat" \
  --query 'NatGateways[0].{NatGatewayId:NatGatewayId,State:State}' \
  --output table
```
**Expected:** NatGatewayId present, State = `available`

---

### Step 7 — Route Tables

```bash
aws ec2 describe-route-tables \
  --filters "Name=tag:Name,Values=nizami-public-rt,nizami-private-rt" \
  --query 'RouteTables[*].{Name:Tags[?Key==`Name`]|[0].Value,RTB:RouteTableId,Associations:length(Associations)}' \
  --output table
```
**Expected:** 2 rows, each with 2 associations (one per subnet)

---

### Step 8 — Security Groups (6 total)

```bash
aws ec2 describe-security-groups \
  --filters "Name=vpc-id,Values=$VPC_ID" \
  --query 'SecurityGroups[*].{Name:GroupName,Id:GroupId}' \
  --output table
```
**Expected:** `sg-alb`, `web-sg`, `sg-worker`, `sg-pgbouncer`, `sg-rds`, `sg-redis` all present

---

### Step 9 — RDS PostgreSQL

```bash
aws rds describe-db-instances \
  --db-instance-identifier nizami-postgres \
  --query 'DBInstances[0].{Status:DBInstanceStatus,Engine:Engine,Class:DBInstanceClass,Endpoint:Endpoint.Address}' \
  --output table
```
**Expected:** Status = `available`, Engine = `postgres`

```bash
# Verify pgVector extension (requires a DB connection — skip if you cannot connect yet)
# psql -h <RDS_ENDPOINT> -U nizami_user -d nizami_db -c "\dx" | grep vector
```

---

### Step 10 — ElastiCache Redis

```bash
aws elasticache describe-cache-clusters \
  --cache-cluster-id nizami-redis \
  --show-cache-node-info \
  --query 'CacheClusters[0].{Status:CacheClusterStatus,Engine:Engine,Endpoint:CacheNodes[0].Endpoint.Address}' \
  --output table
```
**Expected:** Status = `available`, Engine = `redis`

---

### Step 11 — Secrets Manager

```bash
aws secretsmanager list-secrets \
  --filters Key=name,Values=nizami/prod/ \
  --query 'SecretList[*].Name' \
  --output table
```
**Expected:** all of the following are listed:
- `nizami/prod/SECRET_KEY`
- `nizami/prod/DB_PASSWORD`
- `nizami/prod/REDIS_URL`
- `nizami/prod/OPENAI_API_KEY`
- `nizami/prod/EMAIL_HOST_PASSWORD`
- `nizami/prod/MOYASAR_SECRET_KEY`
- `nizami/prod/MOYASAR_WEBHOOK_SECRET_KEY`

---

### Step 12 — IAM Roles

```bash
aws iam get-role --role-name ecsTaskExecutionRole --query 'Role.RoleName' --output text
aws iam get-role --role-name nizami-ecs-task-role --query 'Role.RoleName' --output text
```
**Expected:** both return the role name without error

```bash
# Verify execution role has the secrets policy
aws iam get-role-policy \
  --role-name ecsTaskExecutionRole \
  --policy-name NizamiSecretsAccess \
  --query 'PolicyName' \
  --output text
```
**Expected:** `NizamiSecretsAccess`

---

### Step 13 — ECS Cluster

```bash
aws ecs describe-clusters \
  --clusters nizami-prod \
  --query 'clusters[0].{Name:clusterName,Status:status,RunningTasks:runningTasksCount}' \
  --output table
```
**Expected:** Status = `ACTIVE`

---

### Step 14 — CloudWatch Log Groups

```bash
aws logs describe-log-groups \
  --log-group-name-prefix /ecs/nizami/ \
  --query 'logGroups[*].logGroupName' \
  --output table
```
**Expected:** 6 groups:
- `/ecs/nizami/web`
- `/ecs/nizami/pgbouncer`
- `/ecs/nizami/celery-extraction`
- `/ecs/nizami/celery-rag`
- `/ecs/nizami/celery-summaries`
- `/ecs/nizami/celery-beat`

---

### Step 15 — Cloud Map Namespace

```bash
aws servicediscovery list-namespaces \
  --query 'Namespaces[?Name==`nizami.local`].{Name:Name,Id:Id,Type:Type}' \
  --output table
```
**Expected:** Name = `nizami.local`, Type = `DNS_PRIVATE`

---

### Step 16 — PgBouncer ECS Service

```bash
aws ecs describe-services \
  --cluster nizami-prod \
  --services nizami-pgbouncer-service \
  --query 'services[0].{Status:status,Running:runningCount,Desired:desiredCount}' \
  --output table
```
**Expected:** Status = `ACTIVE`, Running = Desired (usually 1)

```bash
# Verify service discovery is registered
aws servicediscovery list-services \
  --query 'Services[?Name==`pgbouncer`].{Name:Name,Id:Id}' \
  --output table
```
**Expected:** `pgbouncer` listed

---

### Step 17 — Migrate Task Definition

```bash
aws ecs describe-task-definition \
  --task-definition nizami-migrate \
  --query 'taskDefinition.{Family:family,Revision:revision,Status:status}' \
  --output table
```
**Expected:** Family = `nizami-migrate`, Status = `ACTIVE`

---

### Step 18 — Web ECS Service

```bash
aws ecs describe-services \
  --cluster nizami-prod \
  --services nizami-web-service \
  --query 'services[0].{Status:status,Running:runningCount,Desired:desiredCount,TaskDef:taskDefinition}' \
  --output table
```
**Expected:** Status = `ACTIVE`, Running = Desired (usually 2)

---

### Step 19 — Celery ECS Services (4 total)

```bash
aws ecs describe-services \
  --cluster nizami-prod \
  --services \
    nizami-celery-extraction-service \
    nizami-celery-rag-service \
    nizami-celery-summaries-service \
    nizami-celery-beat-service \
  --query 'services[*].{Name:serviceName,Status:status,Running:runningCount,Desired:desiredCount}' \
  --output table
```
**Expected:** all 4 rows, Status = `ACTIVE`, Running = Desired

---

### Step 20 — Application Load Balancer

```bash
aws elbv2 describe-load-balancers \
  --names nizami-alb \
  --query 'LoadBalancers[0].{State:State.Code,DNS:DNSName,Scheme:Scheme}' \
  --output table
```
**Expected:** State = `active`, Scheme = `internet-facing`

```bash
# Verify target group health
TG_ARN=$(aws elbv2 describe-target-groups --names nizami-web-tg --query 'TargetGroups[0].TargetGroupArn' --output text)
aws elbv2 describe-target-health \
  --target-group-arn $TG_ARN \
  --query 'TargetHealthDescriptions[*].{Target:Target.Id,State:TargetHealth.State}' \
  --output table
```
**Expected:** all targets show State = `healthy`

```bash
# Verify ACM certificate is issued
aws acm list-certificates \
  --query 'CertificateSummaryList[?DomainName==`api.app.nizami.ai`].{Domain:DomainName,Status:Status}' \
  --output table
```
**Expected:** Status = `ISSUED`

---

### Step 22 — Route 53 DNS

```bash
# Replace YOUR_ZONE_ID with your hosted zone ID
aws route53 list-resource-record-sets \
  --hosted-zone-id YOUR_ZONE_ID \
  --query 'ResourceRecordSets[?Name==`api.app.nizami.ai.`].{Name:Name,Type:Type}' \
  --output table
```
**Expected:** Type = `A` record exists for `api.app.nizami.ai`

---

## AWS_PRODUCTION_DEPLOY.md Validation

---

### Step 1 — GitHub OIDC Provider

```bash
aws iam list-open-id-connect-providers \
  --query 'OpenIDConnectProviderList[*].Arn' \
  --output table
```
**Expected:** an ARN containing `token.actions.githubusercontent.com`

---

### Step 2 & 3 — GitHub Actions IAM Role

```bash
aws iam get-role \
  --role-name nizami-github-actions-prod \
  --query 'Role.{Name:RoleName,Created:CreateDate}' \
  --output table
```
**Expected:** role exists without error

```bash
# Verify the CI/CD policy is attached
aws iam get-role-policy \
  --role-name nizami-github-actions-prod \
  --policy-name NizamiCICDPolicy \
  --query 'PolicyName' \
  --output text
```
**Expected:** `NizamiCICDPolicy`

```bash
# Verify the trust policy is locked to main branch
aws iam get-role \
  --role-name nizami-github-actions-prod \
  --query 'Role.AssumeRolePolicyDocument' \
  --output json | grep "refs/heads/main"
```
**Expected:** the string `refs/heads/main` appears in the output

---

### Steps 4–6 — GitHub Secrets, Variables, and Environment

These cannot be verified from the CLI — check them in GitHub:

- **Secret**: repo → Settings → Secrets and variables → Actions → `AWS_ROLE_ARN_PROD` exists
- **Variables**: all 16 variables from Step 5 of `AWS_PRODUCTION_DEPLOY.md` are set
- **Environment**: repo → Settings → Environments → `production` exists

---

## End-to-End Smoke Test

Run this after all steps are complete to verify the full stack is reachable:

```bash
# 1. API health check
curl -s https://api.app.nizami.ai/api/v1/health/

# 2. Check all ECS services are stable
aws ecs describe-services \
  --cluster nizami-prod \
  --services \
    nizami-web-service \
    nizami-pgbouncer-service \
    nizami-celery-extraction-service \
    nizami-celery-rag-service \
    nizami-celery-summaries-service \
    nizami-celery-beat-service \
  --query 'services[*].{Name:serviceName,Running:runningCount,Desired:desiredCount}' \
  --output table

# 3. Trigger a test deploy (uses current HEAD)
# Go to GitHub → Actions → Deploy Production → Run workflow
```

**Expected for health check:** `{"status": "ok"}` or similar JSON response

**Expected for ECS table:** Running = Desired for all 6 services
