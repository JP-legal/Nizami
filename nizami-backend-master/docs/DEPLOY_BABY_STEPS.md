# Nizami — Baby Steps to Production on AWS

This document walks through every single step to deploy Nizami on AWS,
explaining each concept from scratch. Every step includes what it is,
why it exists, what breaks if you skip it, and exactly what to do.

Follow in order. Each step depends on the ones before it.

---

## Before You Start — Prerequisites

### What you need on your machine

**AWS account**
Go to aws.amazon.com and create an account. You need a credit card.
Nothing in this guide costs anything until you actually create resources.

**AWS CLI**
The command-line tool that lets you control AWS from your terminal instead
of clicking through the console. Install it:
```bash
# macOS
brew install awscli

# Verify
aws --version
```

**Configure it with your credentials**
```bash
aws configure
```
It will ask for:
- AWS Access Key ID — create this in IAM → Users → your user → Security credentials
- AWS Secret Access Key — shown once when you create the key, save it
- Default region — type `me-south-1`
- Default output format — type `json`

**Docker Desktop**
Your code runs inside Docker containers on AWS. You need Docker locally to
build and test the image before pushing it. Install from docker.com.

**Verify Docker works**
```bash
docker --version
docker compose version
```

---

## Step 1 — Your Docker Image (Code Level)

### What it is
A Docker image is a snapshot of your entire application — the Python
version, all installed packages, your code, and the command to start it.
Think of it as a sealed box that contains everything needed to run your app.

When AWS ECS starts your application, it does not clone your git repo or
install packages — it just opens this sealed box and runs it.

### Why we need it
AWS ECS Fargate has no servers for you to SSH into and set up manually.
It needs a complete, self-contained image to run. Without an image, there
is nothing to deploy.

### What breaks without it
Nothing deploys. ECS has nowhere to pull your application from.

### What to do
First, make sure your local docker-compose works:

```bash
cd nizami-backend-master

# Copy the example env file and fill in your values
cp .env.example .env
# Edit .env — at minimum set OPENAI_API_KEY and SECRET_KEY

# Build and start everything locally
docker compose up --build
```

Open http://localhost:8011/api/v1/health/ in your browser. If it responds,
your image works locally.

### Code impact
No code changes needed. The Dockerfile is already correct:
- Uses Python 3.12
- Installs all dependencies from requirements.txt
- Starts Gunicorn via gunicorn.conf.py

---

## Step 2 — ECR (Where Your Image Lives on AWS)

### What it is
ECR (Elastic Container Registry) is AWS's private Docker image storage.
It's like Docker Hub but private and inside your AWS account.

When ECS starts a task, it pulls the image from ECR. When CI/CD builds a
new version, it pushes the image to ECR.

### Why we need it
ECS cannot pull images from your laptop. It needs the image to be stored
somewhere it can reach. ECR is that place — it's inside AWS so the pull
is fast and free (no egress charges within the same region).

### What breaks without it
ECS cannot start any task. Every task definition references an image URI
like `ACCOUNT.dkr.ecr.me-south-1.amazonaws.com/nizami/backend:latest`.
If that URI does not exist, ECS fails with "image not found".

### What to do

**Create the repository**
```bash
aws ecr create-repository \
  --repository-name nizami/backend \
  --region me-south-1 \
  --image-scanning-configuration scanOnPush=true
```

It will return a `repositoryUri` like:
`123456789.dkr.ecr.me-south-1.amazonaws.com/nizami/backend`
Save this — you will use it everywhere.

**Build and push your image**
```bash
# Get a login token (valid for 12 hours)
aws ecr get-login-password --region me-south-1 | \
  docker login --username AWS --password-stdin \
  123456789.dkr.ecr.me-south-1.amazonaws.com

# Build the image (must be linux/amd64 for ECS Fargate)
docker build --platform linux/amd64 \
  -t 123456789.dkr.ecr.me-south-1.amazonaws.com/nizami/backend:latest \
  nizami-backend-master/

# Push it
docker push 123456789.dkr.ecr.me-south-1.amazonaws.com/nizami/backend:latest
```

**Verify** — go to AWS Console → ECR → nizami/backend → you should see
your image listed with a tag of `latest`.

### Code impact
None. ECR just stores the image your Dockerfile already defines.

---

## Step 3 — VPC (Your Private Network on AWS)

### What it is
A VPC (Virtual Private Cloud) is a completely isolated network that AWS
creates for you. It is your own private section of the AWS cloud where
all your resources live. Nothing outside your VPC can reach anything
inside it unless you explicitly allow it.

Think of it as buying a building. The VPC is the building. The subnets
are the floors. The security groups are the locked doors on each floor.

### Why we need it
Every AWS resource (ECS tasks, RDS, ElastiCache, etc.) must live inside
a network. Without a VPC, you cannot create most resources. The VPC also
lets you control exactly who can talk to what using security groups and
route tables.

### What breaks without it
You cannot create RDS, ElastiCache, ECS services, or a load balancer
without placing them in a VPC. AWS will block the creation.

### What to do

**Create the VPC**
```bash
aws ec2 create-vpc \
  --cidr-block 10.0.0.0/16 \
  --region me-south-1 \
  --tag-specifications 'ResourceType=vpc,Tags=[{Key=Name,Value=nizami-vpc}]'
```

The `--cidr-block 10.0.0.0/16` means your VPC owns all IP addresses from
10.0.0.0 to 10.0.255.255 — that's 65,536 private IP addresses for your
resources to use.

Save the `VpcId` from the output (looks like `vpc-05eda62a4bda29723...`).

**Enable DNS in the VPC** (required for Cloud Map DNS to work)
```bash
aws ec2 modify-vpc-attribute \
  --vpc-id vpc-05eda62a4bda29723 \
  --enable-dns-hostnames

aws ec2 modify-vpc-attribute \
  --vpc-id vpc-05eda62a4bda29723 \
  --enable-dns-support
```

---

## Step 4 — Subnets (Floors Inside Your VPC)

### What it is
Subnets divide your VPC's IP range into smaller sections. Each subnet
lives in one Availability Zone (a physically separate data center).

You create 4 subnets:
- 2 **public** subnets (one per AZ) — for the ALB and NAT Gateway
- 2 **private** subnets (one per AZ) — for everything else

**Public** means the subnet has a route to the internet.
**Private** means it does not — things in it are invisible to the internet.

### Why we need them
- The ALB must be in a public subnet so users can reach it from the internet
- Your app containers must be in private subnets so the internet cannot
  directly attack them — only the ALB can reach them
- Spreading across 2 AZs means if one data center has a problem, the
  other keeps running

### What breaks without private subnets
Your database and application containers would be directly reachable from
the internet, which is a major security risk. Anyone could try to connect
to your database port.

### What breaks with only 1 AZ
If that one data center has a power outage, hardware failure, or
maintenance window, your entire app goes down. With 2 AZs, one data
center failure is invisible to your users.

### What to do

**Create the 4 subnets**

```bash
# Public subnet in AZ a
aws ec2 create-subnet \
  --vpc-id vpc-05eda62a4bda29723 \
  --cidr-block 10.0.0.0/24 \
  --availability-zone me-south-1a \
  --tag-specifications 'ResourceType=subnet,Tags=[{Key=Name,Value=nizami-public-a}]'
# not-working
# Public subnet in AZ b
aws ec2 create-subnet \
  --vpc-id vpc-05eda62a4bda29723 \
  --cidr-block 10.0.1.0/24 \
  --availability-zone me-south-1b \
  --tag-specifications 'ResourceType=subnet,Tags=[{Key=Name,Value=nizami-public-b}]'

# Private subnet in AZ a
aws ec2 create-subnet \
  --vpc-id vpc-05eda62a4bda29723 \
  --cidr-block 10.0.10.0/24 \
  --availability-zone me-south-1a \
  --tag-specifications 'ResourceType=subnet,Tags=[{Key=Name,Value=nizami-private-a}]'

# Private subnet in AZ b
aws ec2 create-subnet \
  --vpc-id vpc-05eda62a4bda29723 \
  --cidr-block 10.0.11.0/24 \
  --availability-zone me-south-1b \
  --tag-specifications 'ResourceType=subnet,Tags=[{Key=Name,Value=nizami-private-b}]'
```

Save all 4 subnet IDs from the outputs.

---

## Step 5 — Internet Gateway (The Building's Front Door)

### What it is
An Internet Gateway is what connects your VPC to the public internet.
Without it, nothing in your VPC can communicate with the outside world
in either direction.

### Why we need it
The ALB needs to receive HTTPS requests from users' browsers. The NAT
Gateway (next step) needs to route outbound traffic from private subnets
to services like OpenAI. Both of these require a path to the internet,
which only the Internet Gateway provides.

### What breaks without it
- Users cannot reach the ALB → no one can use your app
- NAT Gateway cannot function → Celery workers cannot call OpenAI API

### What to do

```bash
# Create the gateway
aws ec2 create-internet-gateway \
  --tag-specifications 'ResourceType=internet-gateway,Tags=[{Key=Name,Value=nizami-igw}]'
# Save the InternetGatewayId

# Attach it to your VPC
aws ec2 attach-internet-gateway \
  --internet-gateway-id igw-0f0d9aaa1776302de \
  --vpc-id vpc-05eda62a4bda29723
```

---

## Step 6 — NAT Gateway (One-Way Door for Private Subnets)

### What it is
A NAT Gateway sits in a public subnet and acts as a proxy for outbound
internet traffic from private subnets.

Your Celery workers (in private subnets) need to call the OpenAI API,
Moyasar, and download packages — all of which require internet access.
But you do not want those workers to be directly reachable from the internet.

NAT solves this: workers send traffic OUT through the NAT Gateway. The
internet sees the NAT Gateway's IP, not the worker's IP. No inbound
connection can be initiated from the internet to your workers.

### Why we need it
Without NAT, private subnet resources have no path to the internet.
Your Celery workers would fail on every OpenAI API call, every S3 request
that goes through the internet path, and any external service call.

### What breaks without it
- `extract_file` task fails — it cannot download files from S3 through
  internet-routed paths
- `generate_final_answer` fails — it cannot reach OpenAI API
- `analyze_reference_document` fails — it cannot reach OpenAI embeddings API
- Subscription renewal fails

### What to do

```bash
# First, allocate an Elastic IP (a fixed public IP for the NAT Gateway)
aws ec2 allocate-address \
  --domain vpc \
  --region me-south-1
# Save the AllocationId

# Create the NAT Gateway in the PUBLIC subnet (not private)
aws ec2 create-nat-gateway \
  --subnet-id subnet-public-a-id \
  --allocation-id eipalloc-08370f4d57d4186bf \
  --tag-specifications 'ResourceType=natgateway,Tags=[{Key=Name,Value=nizami-nat}]'
# Save the NatGatewayId

# Wait for it to be available (takes 1-2 minutes)
aws ec2 wait nat-gateway-available \
  --nat-gateway-ids nat-0ac703f1095a3d48c
```

> Cost note: the NAT Gateway costs ~$43/month just to exist, plus ~$0.045
> per GB of data. This is unavoidable for private subnet outbound access.

---

## Step 7 — Route Tables (Traffic Direction Signs)

### What it is
A route table is a set of rules that tells your subnets where to send
traffic. Think of them as GPS routing rules — "for traffic going to
0.0.0.0/0 (the internet), use this gateway."

### Why we need them
Without route tables, subnets have no path to send traffic anywhere except
within the VPC itself. Your public subnets need a route to the Internet
Gateway. Your private subnets need a route to the NAT Gateway for outbound
traffic.

### What breaks without correct routes
- Public subnets without a route to IGW: the ALB cannot receive traffic
  from users
- Private subnets without a route to NAT: workers cannot reach OpenAI,
  Moyasar, or any external service

### What to do

**Public route table** (routes internet traffic to the Internet Gateway)
```bash
# Create
aws ec2 create-route-table \
  --vpc-id vpc-05eda62a4bda29723 \
  --tag-specifications 'ResourceType=route-table,Tags=[{Key=Name,Value=nizami-public-rt}]'
# Save RouteTableId

# Add route: all internet traffic → Internet Gateway
aws ec2 create-route \
  --route-table-id rtb-public-id \
  --destination-cidr-block 0.0.0.0/0 \
  --gateway-id igw-05eda62a4bda29723

# Attach to BOTH public subnets
aws ec2 associate-route-table \
  --route-table-id rtb-public-id \
  --subnet-id subnet-public-a-id

aws ec2 associate-route-table \
  --route-table-id rtb-public-id \
  --subnet-id subnet-public-b-id
```

**Private route table** (routes internet traffic to the NAT Gateway)
```bash
# Create
aws ec2 create-route-table \
  --vpc-id vpc-05eda62a4bda29723 \
  --tag-specifications 'ResourceType=route-table,Tags=[{Key=Name,Value=nizami-private-rt}]'

# Add route: all internet traffic → NAT Gateway
aws ec2 create-route \
  --route-table-id rtb-private-id \
  --destination-cidr-block 0.0.0.0/0 \
  --nat-gateway-id nat-05eda62a4bda29723

# Attach to BOTH private subnets
aws ec2 associate-route-table \
  --route-table-id rtb-private-id \
  --subnet-id subnet-private-a-id

aws ec2 associate-route-table \
  --route-table-id rtb-private-id \
  --subnet-id subnet-private-b-id
```

---

## Step 8 — Security Groups (The Locked Doors)

### What it is
A security group is a virtual firewall attached to a specific resource.
It has a list of inbound rules: "allow traffic on port X from source Y."
By default everything is denied — you must explicitly allow each type
of connection.

The key insight: security groups reference each other. Instead of saying
"allow port 5432 from IP 10.0.10.5", you say "allow port 5432 from
sg-worker" — meaning any resource in the sg-worker security group can
connect. This works even as task IPs change on every restart.

### Why we need them
Without security groups, AWS blocks all inbound traffic by default — nothing
can talk to anything. You need to explicitly open the paths that should
be allowed.

### What breaks without correct security groups
The failure mode is silent connection timeouts:
- If sg-web does not allow port 8000 from sg-alb: ALB health checks fail,
  no traffic reaches your app, ALB shows all targets as unhealthy
- If sg-pgbouncer does not allow 5432 from sg-web: every database query
  times out — app crashes on startup
- If sg-redis does not allow 6379 from sg-worker: Celery workers cannot
  connect to the broker — all tasks silently fail to queue

### What to do

Create 6 security groups. For each one: create it, then add inbound rules.

```bash
# sg-alb — the ALB faces the internet
aws ec2 create-security-group \
  --group-name sg-alb \
  --description "ALB inbound from internet" \
  --vpc-id vpc-05eda62a4bda29723
# Save GroupId → SG_ALB_ID

aws ec2 authorize-security-group-ingress \
  --group-id sg-004ebc11fe3bb7e38 \
  --protocol tcp --port 443 --cidr 0.0.0.0/0

aws ec2 authorize-security-group-ingress \
  --group-id sg-004ebc11fe3bb7e38 \
  --protocol tcp --port 80 --cidr 0.0.0.0/0

# sg-web — ECS web tasks, only reachable from the ALB
aws ec2 create-security-group \
  --group-name web-sg \
  --description "Web tasks from ALB only" \
  --vpc-id vpc-05eda62a4bda29723
# Save → SG_WEB_ID

aws ec2 authorize-security-group-ingress \
  --group-id sg-0be51edd6231e4a3b \
  --protocol tcp --port 8000 \
  --source-group SG_ALB_ID

# sg-worker — Celery workers, no inbound at all (workers only call outward)
aws ec2 create-security-group \
  --group-name sg-worker \
  --description "Celery workers - no inbound" \
  --vpc-id vpc-05eda62a4bda29723
# Save → SG_WORKER_ID
# (no authorize-security-group-ingress needed)

# sg-pgbouncer — only web and workers can connect
aws ec2 create-security-group \
  --group-name sg-pgbouncer \
  --description "PgBouncer from web and workers" \
  --vpc-id vpc-05eda62a4bda29723
# Save → SG_PGBOUNCER_ID

aws ec2 authorize-security-group-ingress \
  --group-id SG_PGBOUNCER_ID \
  --protocol tcp --port 5432 \
  --source-group SG_WEB_ID

aws ec2 authorize-security-group-ingress \
  --group-id SG_PGBOUNCER_ID \
  --protocol tcp --port 5432 \
  --source-group SG_WORKER_ID

# sg-rds — only PgBouncer can connect to Postgres
aws ec2 create-security-group \
  --group-name sg-rds \
  --description "RDS from PgBouncer only" \
  --vpc-id vpc-05eda62a4bda29723
# Save → SG_RDS_ID

aws ec2 authorize-security-group-ingress \
  --group-id SG_RDS_ID \
  --protocol tcp --port 5432 \
  --source-group SG_PGBOUNCER_ID

# sg-redis — only web and workers can connect to Redis
aws ec2 create-security-group \
  --group-name sg-redis \
  --description "Redis from web and workers" \
  --vpc-id vpc-05eda62a4bda29723
# Save → SG_REDIS_ID

aws ec2 authorize-security-group-ingress \
  --group-id SG_REDIS_ID \
  --protocol tcp --port 6379 \
  --source-group SG_WEB_ID

aws ec2 authorize-security-group-ingress \
  --group-id SG_REDIS_ID \
  --protocol tcp --port 6379 \
  --source-group SG_WORKER_ID
```

---

## Step 9 — RDS PostgreSQL (The Database)

### What it is
Amazon RDS is a managed PostgreSQL database. "Managed" means AWS handles
OS patching, backups, hardware replacement, and failover automatically.
You just connect to a hostname and use it like any PostgreSQL server.

Nizami uses the pgVector extension to store document embeddings — vectors
that represent the semantic meaning of legal text, used to find relevant
documents during a RAG query.

### Why we need it
Your app stores all data (users, chats, messages, subscriptions, document
metadata) in PostgreSQL. Without it, nothing persists — every request would
fail trying to read or write data.

### What breaks without the pgVector extension specifically
The `analyze_reference_document` Celery task will fail when it tries to
store embeddings. The `similarity_search_with_document_filter` function in
`retrievers.py` will fail on every RAG query. The entire legal document
search feature breaks.

### What to do

**Create a subnet group** (tells RDS which subnets it can use)
```bash
aws rds create-db-subnet-group \
  --db-subnet-group-name nizami-db-subnet-group \
  --db-subnet-group-description "Nizami RDS subnets" \
  --subnet-ids subnet-private-a-id subnet-private-b-id
```

**Create the RDS instance**
```bash
aws rds create-db-instance \
  --db-instance-identifier nizami-postgres \
  --db-instance-class db.t4g.medium \
  --engine postgres \
  --engine-version 16 \
  --master-username nizami_user \
  --master-user-password YOUR_STRONG_PASSWORD \
  --db-name nizami_db \
  --db-subnet-group-name nizami-db-subnet-group \
  --vpc-security-group-ids SG_RDS_ID \
  --storage-type gp3 \
  --allocated-storage 100 \
  --backup-retention-period 14 \
  --multi-az \
  --no-publicly-accessible \
  --region me-south-1
```

This takes 5–10 minutes. Wait for it:
```bash
aws rds wait db-instance-available \
  --db-instance-identifier nizami-postgres
```

Get the endpoint:
```bash
aws rds describe-db-instances \
  --db-instance-identifier nizami-postgres \
  --query 'DBInstances[0].Endpoint.Address' \
  --output text
```
Save this — it looks like `nizami-postgres.abc123.me-south-1.rds.amazonaws.com`

**Enable pgVector extension** — you need to connect to the database once
to run this. Use a temporary EC2 instance or a bastion host since RDS is
in a private subnet. Or use the AWS Systems Manager session manager with
a bastion. Alternatively, run it from a one-off ECS task after the cluster
is ready:
```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
```

### Code impact
In your `.env` for production:
```
DB_HOST=pgbouncer.nizami.local   ← NOT the RDS endpoint directly
DB_DATABASE=nizami_db
DB_USERNAME=nizami_user
DB_PORT=5432
```
The app always connects to PgBouncer (Step 12), which then connects to
RDS. Your code never changes — only the host changes between environments.

---

## Step 10 — ElastiCache Redis (Celery's Message Queue)

### What it is
Amazon ElastiCache Redis is a managed in-memory data store. In Nizami it
serves as the **Celery broker** — the central message queue where tasks
wait to be picked up by workers.

When your web server calls `extract_file.apply_async(...)`, it does not
run the function. It writes a message to Redis that says "please run
extract_file with these arguments." The Celery extraction worker reads
that message from Redis and executes the function.

### Why we need it
Without Redis, Celery has nowhere to queue tasks. The old Django-Q setup
used PostgreSQL as the broker (slower, adds DB load). Redis is purpose-built
for this — it's a fast in-memory queue that can handle thousands of task
messages per second.

### What breaks without it
- Every `apply_async` call in your code will fail with a connection error
- File extraction stops working
- Document embeddings stop working
- Final answer generation stops working
- The entire async task system is dead

If Redis goes down mid-operation, tasks that were queued but not yet
picked up are lost (unless you enable Redis persistence). Workers that were
executing a task continue to completion because they already have the task
in memory.

### What to do

**Create a subnet group for ElastiCache**
```bash
aws elasticache create-cache-subnet-group \
  --cache-subnet-group-name nizami-redis-subnet-group \
  --cache-subnet-group-description "Nizami Redis subnets" \
  --subnet-ids subnet-private-a-id subnet-private-b-id
```

**Create the Redis cluster**
```bash
aws elasticache create-cache-cluster \
  --cache-cluster-id nizami-redis \
  --cache-node-type cache.t4g.small \
  --engine redis \
  --engine-version 7.0 \
  --num-cache-nodes 1 \
  --cache-subnet-group-name nizami-redis-subnet-group \
  --security-group-ids sg-0891a25ff8a52c7ba \
  --region me-south-1
```

Wait for it to be available:
```bash
aws elasticache wait cache-cluster-available \
  --cache-cluster-id nizami-redis
```

Get the endpoint:
```bash
aws elasticache describe-cache-clusters \
  --cache-cluster-id nizami-redis \
  --show-cache-node-info \
  --query 'CacheClusters[0].CacheNodes[0].Endpoint.Address' \
  --output text
```

Your `REDIS_URL` for production will be:
```redis://nizami-redis.hmy6pz.0001.mes1.cache.amazonaws.com:6379/0

redis://nizami-redis.abc123.cfg.me-south-1.cache.amazonaws.com:6379/0
```

> Note: Use `redis://` for ElastiCache without TLS (simpler for getting
> started). Enable TLS later and switch to `rediss://` for production security.

### Code impact
In `src/settings.py`, the Celery broker config already reads from the
environment:
```python
CELERY_BROKER_URL = env('REDIS_URL', default='redis://redis:6379/0')
```
In production, set `REDIS_URL` to the ElastiCache endpoint via Secrets
Manager. No code change needed.

---

## Step 11 — Secrets Manager (Safe Storage for Passwords and Keys)

### What it is
AWS Secrets Manager is a secure vault for sensitive values like database
passwords, API keys, and JWT secret keys. ECS task definitions reference
secrets by their ARN — AWS injects the value as an environment variable
when the task starts. The value never appears in logs, the ECS console,
or the task definition JSON.

### Why we need it
If you put your database password directly in the ECS task definition
environment block, it appears in plain text in the AWS console and in any
CloudTrail logs. Anyone with read access to ECS task definitions sees it.
Secrets Manager encrypts the value using KMS and only decrypts it at
task startup.

### What breaks without it
Nothing breaks technically if you use plain environment variables. But it
is a critical security risk — your OpenAI API key, database password, and
JWT secret key would be readable by anyone who has AWS console access.
If your AWS account is compromised, an attacker instantly has all your
credentials.

### What to do

Create each secret as a plain-text string (not JSON for simplicity):

```bash
# Django secret key
aws secretsmanager create-secret \
  --name nizami/prod/SECRET_KEY \
  --secret-string "$(python3 -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())")" \
  --region me-south-1

# Database password (use the same password you set in Step 9)
aws secretsmanager create-secret \
  --name nizami/prod/DB_PASSWORD \
  --secret-string "YOUR_STRONG_RDS_PASSWORD" \
  --region me-south-1

# Redis URL
aws secretsmanager create-secret \
  --name nizami/prod/REDIS_URL \
  --secret-string "redis://nizami-redis.abc123.cfg.me-south-1.cache.amazonaws.com:6379/0" \
  --region me-south-1

# OpenAI key
aws secretsmanager create-secret \
  --name nizami/prod/OPENAI_API_KEY \
  --secret-string "sk-..." \
  --region me-south-1

# Email password
aws secretsmanager create-secret \
  --name nizami/prod/EMAIL_HOST_PASSWORD \
  --secret-string "your-smtp-password" \
  --region me-south-1

# Moyasar keys
aws secretsmanager create-secret \
  --name nizami/prod/MOYASAR_SECRET_KEY \
  --secret-string "sk_live_..." \
  --region me-south-1

aws secretsmanager create-secret \
  --name nizami/prod/MOYASAR_WEBHOOK_SECRET_KEY \
  --secret-string "..." \
  --region me-south-1
```

Each command prints an ARN like:
`arn:aws:secretsmanager:me-south-1:123456789:secret:nizami/prod/SECRET_KEY-AbCdEf`

Save all ARNs — you paste them into ECS task definitions in Step 16.

---

## Step 12 — IAM Roles (Permissions for AWS Services to Talk to Each Other)

### What it is
IAM (Identity and Access Management) roles define what an AWS service is
allowed to do. There are two roles needed for ECS:

**Execution Role** — ECS itself uses this to pull your Docker image from
ECR and to read secrets from Secrets Manager at task startup. This role
is standard and AWS provides a managed version.

**Task Role** — your application code uses this at runtime. When your
Celery worker calls `boto3.client('s3').put_object(...)`, boto3 checks
this role for permission to write to S3. You define exactly which S3
buckets and actions your code is allowed to perform.

### Why we need them
Without the execution role, ECS cannot pull your image from ECR — tasks
fail to start immediately with "unable to pull image."

Without the task role, your code has no permission to access S3, so every
file upload and download fails with an access denied error.

### What breaks without them
- No execution role → tasks cannot start (image pull fails)
- No task role → S3 operations fail → file extraction fails → nothing works

### What to do

**Execution role** — use the AWS-managed one (already exists in your account):
```
arn:aws:iam::ACCOUNT:role/ecsTaskExecutionRole
```

If it does not exist, create it:
```bash
aws iam create-role \
  --role-name ecsTaskExecutionRole \
  --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Principal": {"Service": "ecs-tasks.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }]
  }'

aws iam attach-role-policy \
  --role-name ecsTaskExecutionRole \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
```

You also need to allow it to read your secrets:
```bash
aws iam put-role-policy \
  --role-name ecsTaskExecutionRole \
  --policy-name NizamiSecretsAccess \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": "arn:aws:secretsmanager:me-south-1:ACCOUNT:secret:nizami/prod/*"
    }]
  }'
```

**Task role** — your application code's runtime permissions:
```bash
aws iam create-role \
  --role-name nizami-ecs-task-role \
  --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Principal": {"Service": "ecs-tasks.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }]
  }'

aws iam put-role-policy \
  --role-name nizami-ecs-task-role \
  --policy-name NizamiS3Access \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"],
      "Resource": [
        "arn:aws:s3:::nizami-users-input-documents-bucket",
        "arn:aws:s3:::nizami-users-input-documents-bucket/*"
      ]
    }]
  }'
```

Save both role ARNs for use in task definitions.

### Code impact
None. boto3 (the AWS SDK used by django-storages) automatically detects
and uses the task role when running inside ECS. You do not set
`AWS_ACCESS_KEY_ID` or `AWS_SECRET_ACCESS_KEY` in your production task
definitions — the role replaces them.

---

## Step 13 — ECS Cluster (The Container Orchestrator)

### What it is
An ECS cluster is the management layer that decides where and how to run
your containers. With Fargate, you do not manage any servers — you tell
ECS "I want 2 copies of this container with 1 vCPU and 2 GB RAM" and AWS
finds the hardware, starts the container, monitors it, and restarts it if
it crashes.

FARGATE_SPOT is a cheaper capacity option — AWS runs your task on spare
capacity and can reclaim it with a 2-minute warning. It is 60–70% cheaper
and suitable for Celery workers (they are interruptible — tasks re-queue
on the next available worker).

### Why we need it
Without the cluster, there is nowhere to register services or run tasks.
It is the grouping container for all your ECS services.

### What breaks without it
You cannot create ECS services or run tasks. The cluster must exist first.

### What to do
```bash
aws ecs create-cluster \
  --cluster-name nizami-prod \
  --capacity-providers FARGATE FARGATE_SPOT \
  --default-capacity-provider-strategy \
    capacityProvider=FARGATE,weight=1 \
  --region me-south-1
```

---

## Step 14 — CloudWatch Log Groups (Where Container Logs Go)

### What it is
When your containers write to stdout/stderr, ECS captures those logs and
sends them to CloudWatch Log Groups. This is where you go when something
breaks in production — you read the logs to understand what happened.

Gunicorn, Django, and all Celery workers already write to stdout. The
`gunicorn.conf.py` configures `accesslog = "-"` and `errorlog = "-"`
(both stdout). No code changes needed.

### Why we need them
ECS tasks fail to start if the configured log group does not exist. Also,
without logs you are flying blind — any error in production is invisible.

### What breaks without them
ECS task startup fails with "Error creating container: ...log group does
not exist." All your tasks fail to start.

### What to do
```bash
for service in web pgbouncer celery-extraction celery-rag celery-summaries celery-beat; do
  aws logs create-log-group \
    --log-group-name /ecs/nizami/$service \
    --region me-south-1

  aws logs put-retention-policy \
    --log-group-name /ecs/nizami/$service \
    --retention-in-days 30
done
```

30-day retention means logs older than 30 days are automatically deleted,
keeping your CloudWatch costs predictable.

---

## Step 15 — Cloud Map (Internal DNS Between Services)

### What it is
AWS Cloud Map is a service registry. When PgBouncer starts as an ECS task,
it gets a dynamic IP like `10.0.10.47`. When it restarts (due to a health
check failure or update), it gets a new IP like `10.0.10.112`.

Without Cloud Map, your web and worker tasks would need to know this dynamic
IP in advance — impossible. Cloud Map solves this by giving PgBouncer a
stable DNS name: `pgbouncer.nizami.local`. When your app connects to that
name, Cloud Map resolves it to whatever IP the current PgBouncer task is
running at.

### Why we need it
ECS tasks get new IPs every restart. Your app needs a stable address to
connect to PgBouncer. Cloud Map provides that stable DNS name inside the
VPC.

### What breaks without it
You would have to hardcode PgBouncer's IP in every task definition. Every
time PgBouncer restarts (which ECS does automatically for updates and
health check failures), you would have to manually find the new IP and
update all task definitions and redeploy every service. Completely
unmanageable.

### What to do
```bash
# Create the private DNS namespace
aws servicediscovery create-private-dns-namespace \
  --name nizami.local \
  --vpc vpc-05eda62a4bda29723 \
  --region me-south-1
# Save the NamespaceId from the output
```

Later when you create the PgBouncer ECS service, you enable service
discovery with service name `pgbouncer`. ECS automatically registers/
deregisters task IPs in DNS as tasks start and stop.

---

## Step 16 — PgBouncer ECS Service (Database Connection Pooler)

### What it is
PgBouncer is a lightweight connection pooler. It sits between your app and
PostgreSQL. Your app connects to PgBouncer on port 5432, and PgBouncer
maintains a small pool of real connections to PostgreSQL.

Your app thinks it has its own connection. PgBouncer reuses real Postgres
connections across many app connections using transaction-mode pooling —
a real connection is only held for the duration of one transaction, then
returned to the pool.

**Without PgBouncer:**
- 2 web tasks × 3 Gunicorn workers × 2 threads each = ~12 connections
- 2 extraction workers × 2 concurrency = 4 connections
- 2 RAG workers × 2 concurrency = 4 connections
- Plus Django-celery-beat, summaries = more connections
- Total: 25+ permanent connections to Postgres

**With PgBouncer:** only 20 real connections total, shared across everything.

### What breaks without it
Under moderate load, PostgreSQL hits its connection limit and starts
rejecting new connections with "FATAL: sorry, too many clients already."
Your app returns 500 errors to users.

### What to do

**Register the task definition**

Save this as `pgbouncer-task.json` and replace `ACCOUNT`, `SG_PGBOUNCER_ID`,
and the secret ARN:

```json
{
  "family": "nizami-pgbouncer",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "256",
  "memory": "512",
  "executionRoleArn": "arn:aws:iam::ACCOUNT:role/ecsTaskExecutionRole",
  "containerDefinitions": [{
    "name": "pgbouncer",
    "image": "edoburu/pgbouncer:latest",
    "portMappings": [{ "containerPort": 5432, "protocol": "tcp" }],
    "environment": [
      { "name": "DB_HOST", "value": "nizami-postgres.abc123.me-south-1.rds.amazonaws.com" },
      { "name": "DB_PORT", "value": "5432" },
      { "name": "DB_NAME", "value": "nizami_db" },
      { "name": "DB_USER", "value": "nizami_user" },
      { "name": "POOL_MODE", "value": "transaction" },
      { "name": "MAX_CLIENT_CONN", "value": "100" },
      { "name": "DEFAULT_POOL_SIZE", "value": "20" },
      { "name": "SERVER_RESET_QUERY", "value": "DISCARD ALL" }
    ],
    "secrets": [{
      "name": "DB_PASSWORD",
      "valueFrom": "arn:aws:secretsmanager:me-south-1:ACCOUNT:secret:nizami/prod/DB_PASSWORD-AbCdEf"
    }],
    "logConfiguration": {
      "logDriver": "awslogs",
      "options": {
        "awslogs-group": "/ecs/nizami/pgbouncer",
        "awslogs-region": "me-south-1",
        "awslogs-stream-prefix": "pgbouncer"
      }
    }
  }]
}
```

```bash
aws ecs register-task-definition \
  --cli-input-json file://pgbouncer-task.json \
  --region me-south-1
```

**Create the service discovery entry**
```bash
aws servicediscovery create-service \
  --name pgbouncer \
  --namespace-id ns-05eda62a4bda29723 \
  --dns-config 'NamespaceId=ns-05eda62a4bda29723,DnsRecords=[{Type=A,TTL=10}]' \
  --health-check-custom-config FailureThreshold=1
# Save ServiceId
```

**Create the ECS service**
```bash
aws ecs create-service \
  --cluster nizami-prod \
  --service-name nizami-pgbouncer-service \
  --task-definition nizami-pgbouncer \
  --desired-count 1 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-private-a-id,subnet-private-b-id],securityGroups=[SG_PGBOUNCER_ID],assignPublicIp=DISABLED}" \
  --service-registries "registryArn=arn:aws:servicediscovery:me-south-1:ACCOUNT:service/srv-05eda62a4bda29723" \
  --region me-south-1
```

Wait until stable:
```bash
aws ecs wait services-stable \
  --cluster nizami-prod \
  --services nizami-pgbouncer-service
```

At this point `pgbouncer.nizami.local:5432` resolves inside the VPC.

---

## Step 17 — Run Database Migrations (One-Off Task)

### What it is
Before starting the web or Celery services, you need Django to create all
database tables. This includes the standard Django tables, all your app
tables, and the new `django_celery_beat` tables for the periodic task
scheduler.

A one-off ECS task runs `python manage.py migrate`, exits, and is never
restarted. This is the production equivalent of running migrate manually.

### Why we need it before starting web/workers
If the web service starts before migrations run, every request that touches
the database fails because the tables do not exist yet. `django_celery_beat`
tables must exist before `celery-beat` starts.

### What to do

Save `migrate-task.json` (same image as web, different command):

```json
{
  "family": "nizami-migrate",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "512",
  "memory": "1024",
  "taskRoleArn": "arn:aws:iam::ACCOUNT:role/nizami-ecs-task-role",
  "executionRoleArn": "arn:aws:iam::ACCOUNT:role/ecsTaskExecutionRole",
  "containerDefinitions": [{
    "name": "web",
    "image": "ACCOUNT.dkr.ecr.me-south-1.amazonaws.com/nizami/backend:latest",
    "command": ["python", "manage.py", "migrate", "--noinput"],
    "environment": [
      { "name": "DEBUG", "value": "False" },
      { "name": "DB_HOST", "value": "pgbouncer.nizami.local" },
      { "name": "DB_PORT", "value": "5432" },
      { "name": "DB_DATABASE", "value": "nizami_db" },
      { "name": "DB_USERNAME", "value": "nizami_user" }
    ],
    "secrets": [
      { "name": "SECRET_KEY", "valueFrom": "arn:...:nizami/prod/SECRET_KEY-..." },
      { "name": "DB_PASSWORD", "valueFrom": "arn:...:nizami/prod/DB_PASSWORD-..." },
      { "name": "REDIS_URL", "valueFrom": "arn:...:nizami/prod/REDIS_URL-..." },
      { "name": "OPENAI_API_KEY", "valueFrom": "arn:...:nizami/prod/OPENAI_API_KEY-..." }
    ],
    "logConfiguration": {
      "logDriver": "awslogs",
      "options": {
        "awslogs-group": "/ecs/nizami/web",
        "awslogs-region": "me-south-1",
        "awslogs-stream-prefix": "migrate"
      }
    }
  }]
}
```

```bash
aws ecs register-task-definition \
  --cli-input-json file://migrate-task.json

# Run the migration
TASK_ARN=$(aws ecs run-task \
  --cluster nizami-prod \
  --task-definition nizami-migrate \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-private-a-id],securityGroups=[SG_WEB_ID],assignPublicIp=DISABLED}" \
  --query 'tasks[0].taskArn' --output text)

# Wait for it to finish
aws ecs wait tasks-stopped \
  --cluster nizami-prod \
  --tasks $TASK_ARN

# Check exit code — must be 0
aws ecs describe-tasks \
  --cluster nizami-prod \
  --tasks $TASK_ARN \
  --query 'tasks[0].containers[0].exitCode'
```

If exit code is 0, migrations succeeded. Check logs in CloudWatch:
`/ecs/nizami/web` with prefix `migrate`.

---

## Step 18 — Web ECS Service (The Django API)

### What it is
This is the main Django application. It runs Gunicorn with Uvicorn workers
to handle HTTP requests. The ALB (Step 20) routes user traffic to this
service.

The task definition is the blueprint. The ECS service uses that blueprint
to run 2 copies (tasks) and keep them running permanently.

### What to do

Register `web-task.json` (full version is in `AWS_PRODUCTION_DEPLOY.md`):

```bash
aws ecs register-task-definition \
  --cli-input-json file://web-task.json

aws ecs create-service \
  --cluster nizami-prod \
  --service-name nizami-web-service \
  --task-definition nizami-web \
  --desired-count 2 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-private-a-id,subnet-private-b-id],securityGroups=[SG_WEB_ID],assignPublicIp=DISABLED}" \
  --region me-south-1
  # Note: --load-balancers added after ALB target group is created in Step 20
```

---

## Step 19 — Celery Worker ECS Services

### What it is
Four separate ECS services, each running a specialized Celery worker:

| Service | Handles | Why separate |
|---------|---------|--------------|
| celery-extraction | File download + text extraction | CPU/memory heavy — Aspose, PyMuPDF |
| celery-rag | OpenAI LLM calls for answers | Network-bound, can have 2 concurrent callers |
| celery-summaries | OpenAI embedding + pgVector write | Sequential writes to pgVector — keep concurrency at 1 |
| celery-beat | Fires the subscription renewal task every 2h | Must run exactly once — never scale it |

By keeping these on separate queues, a flood of file uploads (extraction
queue) does not starve the RAG queue — users who ask questions still get
answers while uploads process in the background.

### What breaks with a single worker (the old Django-Q setup)
One worker handles extraction AND LLM calls AND embeddings sequentially.
During a bulk document upload, the single worker is busy extracting files
for minutes. Any user asking a question (which needs the RAG queue) waits
behind all the extraction jobs. Users experience long delays or timeouts
on chat even when their question has nothing to do with document uploads.

### What to do

Register all 4 task definitions and create all 4 services.
See the full JSON in `AWS_PRODUCTION_DEPLOY.md` Step 12. The pattern is the
same for all four — only `family`, `command`, `cpu`, `memory`, and the
log prefix differ.

```bash
# Register task definitions
for worker in celery-extraction celery-rag celery-summaries celery-beat; do
  aws ecs register-task-definition \
    --cli-input-json file://${worker}-task.json
done

# Create services
aws ecs create-service \
  --cluster nizami-prod \
  --service-name nizami-celery-extraction-service \
  --task-definition nizami-celery-extraction \
  --desired-count 2 \
  --capacity-provider-strategy capacityProvider=FARGATE_SPOT,weight=1 \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-private-a-id,subnet-private-b-id],securityGroups=[SG_WORKER_ID],assignPublicIp=DISABLED}"

# Repeat for rag (desired-count 2), summaries (1), beat (1 - FARGATE not SPOT)
```

For `celery-beat` use `--launch-type FARGATE` (not SPOT) because if AWS
reclaims the beat task mid-sleep, the next scheduled run might be missed.

---

## Step 20 — Application Load Balancer

### What it is
The ALB is the public-facing entry point for all API requests. It does
three things simultaneously:
1. **Terminates HTTPS** — your containers speak plain HTTP; the ALB handles
   TLS certificates and decrypts incoming traffic
2. **Load balances** — distributes requests across your 2+ web tasks,
   always sending to the healthiest ones
3. **Health checks** — calls `/api/v1/health/` every 30 seconds on each
   task. If a task fails 3 checks in a row, the ALB stops sending it
   traffic. ECS detects the unhealthy task and replaces it.

### Why we need it
Without the ALB:
- You would need to expose your web containers directly to the internet
  (security risk)
- You would need to handle TLS yourself in your containers
- There would be no health checking — a crashed container would keep
  receiving traffic until you noticed manually

### What breaks without it
No way to reach your API over HTTPS. Browsers block HTTP for any domain
with a certificate — your frontend would fail to call the backend API.

### What to do

**Request a TLS certificate** (free from AWS Certificate Manager):
```bash
aws acm request-certificate \
  --domain-name api.app.nizami.ai \
  --validation-method DNS \
  --region me-south-1
```

The command prints a CNAME record you must add to your DNS (Route 53).
Add it and wait a few minutes for ACM to validate. Save the certificate ARN.

**Create the ALB**
```bash
aws elbv2 create-load-balancer \
  --name nizami-alb \
  --subnets subnet-public-a-id subnet-public-b-id \
  --security-groups SG_ALB_ID \
  --scheme internet-facing \
  --type application \
  --region me-south-1
# Save LoadBalancerArn and DNSName
```

**Create the target group** (where the ALB sends healthy traffic):
```bash
aws elbv2 create-target-group \
  --name nizami-web-tg \
  --protocol HTTP \
  --port 8000 \
  --vpc-id vpc-05eda62a4bda29723 \
  --target-type ip \
  --health-check-path /api/v1/health/ \
  --health-check-interval-seconds 30 \
  --healthy-threshold-count 2 \
  --unhealthy-threshold-count 3 \
  --deregistration-delay-timeout-seconds 60
# Save TargetGroupArn
```

**Create listeners**
```bash
# Port 80 → redirect to 443
aws elbv2 create-listener \
  --load-balancer-arn ALB_ARN \
  --protocol HTTP --port 80 \
  --default-actions Type=redirect,RedirectConfig="{Protocol=HTTPS,Port=443,StatusCode=HTTP_301}"

# Port 443 → forward to target group
aws elbv2 create-listener \
  --load-balancer-arn ALB_ARN \
  --protocol HTTPS --port 443 \
  --certificates CertificateArn=ACM_CERT_ARN \
  --default-actions Type=forward,TargetGroupArn=TARGET_GROUP_ARN
```

**Set idle timeout** to 120 seconds (must be less than Gunicorn's
`timeout=180`):
```bash
aws elbv2 modify-load-balancer-attributes \
  --load-balancer-arn ALB_ARN \
  --attributes Key=idle_timeout.timeout_seconds,Value=120
```

**Update the web ECS service to use the ALB**
```bash
aws ecs update-service \
  --cluster nizami-prod \
  --service nizami-web-service \
  --load-balancers "targetGroupArn=TARGET_GROUP_ARN,containerName=web,containerPort=8000"
```

---

## Step 21 — S3 + CloudFront for Angular Frontends

### What it is
The Angular apps (user frontend and admin panel) are compiled into static
HTML, CSS, and JavaScript files. They do not need a running server — they
just need to be served as files.

S3 stores the files. CloudFront is a CDN (Content Delivery Network) that
caches and serves them from edge locations around the world, faster and
cheaper than serving from a single region.

### Why we need it
Running the frontend inside containers wastes ECS compute just to serve
static files. S3 + CloudFront is cheaper (cents per month vs dollars for
ECS tasks), faster for users globally, and automatically scales to any
traffic level.

### What breaks without it
You would need to run frontend containers in ECS, which adds cost and
complexity for no benefit.

### What to do

**Create the S3 buckets**
```bash
# User frontend
aws s3api create-bucket \
  --bucket nizami-frontend-prod \
  --region me-south-1 \
  --create-bucket-configuration LocationConstraint=me-south-1

# Block all public access (CloudFront handles serving)
aws s3api put-public-access-block \
  --bucket nizami-frontend-prod \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

# Repeat for admin bucket
aws s3api create-bucket \
  --bucket nizami-admin-prod \
  --region me-south-1 \
  --create-bucket-configuration LocationConstraint=me-south-1
```

**Create CloudFront distributions** via the console (it is complex to do
via CLI). In the AWS console:
1. Go to CloudFront → Create distribution
2. Origin: select your S3 bucket, enable Origin Access Control (OAC)
3. Default root object: `index.html`
4. Error pages: 404 → `/index.html` with 200 response code (required for
   Angular's client-side routing — without this, refreshing on any route
   other than `/` returns a 404)
5. HTTPS: attach the ACM certificate for `app.nizami.ai`
6. Create the distribution, save the distribution ID

**Upload the Angular build**
```bash
cd nizami-frontend-main
npm ci
npm run build -- --configuration=production

aws s3 sync dist/nizami-frontend/browser/ s3://nizami-frontend-prod/ --delete

aws cloudfront create-invalidation \
  --distribution-id EXXXXXXXXXXXXXX \
  --paths "/index.html"
```

---

## Step 22 — Route 53 DNS (Pointing Your Domain to AWS)

### What it is
Route 53 is AWS's DNS service. DNS translates domain names like
`api.app.nizami.ai` into the IP address of your ALB. Without this,
users cannot find your app even though it's running perfectly.

### What breaks without it
Users type `api.app.nizami.ai` into their browser and get "DNS resolution
failed." Your app is running, but no one can reach it because there's no
DNS record pointing to it.

### What to do

In the AWS console → Route 53 → Hosted zones → nizami.ai:

**Create alias records** (not CNAME — alias records are free and resolve
faster for AWS resources):

```bash
# API endpoint → ALB
aws route53 change-resource-record-sets \
  --hosted-zone-id YOUR_ZONE_ID \
  --change-batch '{
    "Changes": [{
      "Action": "CREATE",
      "ResourceRecordSet": {
        "Name": "api.app.nizami.ai",
        "Type": "A",
        "AliasTarget": {
          "HostedZoneId": "ALB_HOSTED_ZONE_ID",
          "DNSName": "nizami-alb-123456.me-south-1.elb.amazonaws.com",
          "EvaluateTargetHealth": true
        }
      }
    }]
  }'
```

Repeat for `app.nizami.ai → CloudFront frontend` and
`admin.app.nizami.ai → CloudFront admin`.

---

## Step 23 — CI/CD: Automating Every Future Deploy

### What it is
A CI/CD pipeline (GitHub Actions) automatically builds, tests, and deploys
your app every time you push to `main`. Without it, every deploy is
manual — 15+ steps you must run perfectly in the right order every time.

### What to do

1. Configure OIDC as described in `AWS_PRODUCTION_DEPLOY.md` Step 16
2. Add all GitHub variables from Step 17 of that doc
3. Create `.github/workflows/deploy-production.yml` from Step 18 of that doc

From that point on: `git push origin main` → GitHub Actions → new image
built → migrations run → ECS services updated → frontends deployed.
Zero manual steps.

---

## The First Deploy — Complete Sequence

Run these in order, waiting for each to complete before starting the next:

```
1.  ECR repository created                    ✓ Step 2
2.  Docker image built and pushed to ECR      ✓ Step 2
3.  VPC created                               ✓ Step 3
4.  4 subnets created                         ✓ Step 4
5.  Internet Gateway created + attached       ✓ Step 5
6.  NAT Gateway created                       ✓ Step 6
7.  Route tables created + associated         ✓ Step 7
8.  6 security groups created                 ✓ Step 8
9.  RDS created, pgVector extension enabled   ✓ Step 9
10. ElastiCache Redis created                 ✓ Step 10
11. All secrets stored in Secrets Manager     ✓ Step 11
12. IAM roles created                         ✓ Step 12
13. ECS cluster created                       ✓ Step 13
14. CloudWatch log groups created             ✓ Step 14
15. Cloud Map namespace created               ✓ Step 15
16. PgBouncer task definition + service       ✓ Step 16
    → wait for service stable
17. Run migrations task → wait exit code 0   ✓ Step 17
18. Web task definition + service             ✓ Step 18
19. 4 Celery task definitions + services      ✓ Step 19
20. ACM certificate validated                 ✓ Step 20
21. ALB + target group + listeners            ✓ Step 20
22. Web service updated with ALB              ✓ Step 20
23. S3 buckets + CloudFront distributions    ✓ Step 21
24. Angular builds uploaded to S3            ✓ Step 21
25. Route 53 DNS records created             ✓ Step 22
26. Smoke test: open app.nizami.ai, log in,
    send a message, verify response arrives
```

---

## How to Debug When Something Goes Wrong

**Task fails to start**
```bash
aws ecs describe-tasks --cluster nizami-prod --tasks TASK_ARN \
  --query 'tasks[0].stoppedReason'
```
Common reasons: image not found in ECR, secret ARN wrong, log group missing,
security group blocking something.

**Container starts but crashes**
Check CloudWatch logs:
```bash
aws logs tail /ecs/nizami/web --follow
```

**ALB shows all targets unhealthy**
Your web service is running but the health check at `/api/v1/health/` is
failing. Check web logs. Most common cause: database connection failing
because PgBouncer is not reachable (security group rule missing or Cloud
Map DNS not resolving).

**Celery tasks not being processed**
Check worker logs:
```bash
aws logs tail /ecs/nizami/celery-extraction --follow
```
Most common cause: `REDIS_URL` secret is wrong or Redis security group
blocks the worker.

**Tasks queuing but never completing**
The worker is connecting to Redis (good) but a task is failing partway
through. Look for Python tracebacks in the worker logs.
