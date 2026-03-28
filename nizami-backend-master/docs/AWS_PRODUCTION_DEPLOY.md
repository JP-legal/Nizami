# Nizami — Production CI/CD Setup (GitHub Actions → ECS)

This document covers the one-time setup that makes
`.github/workflows/deploy-production.yml` work.

Every merge to `main` will then automatically:
1. Build the backend Docker image and push it to ECR
2. Run `python manage.py migrate` as a one-off ECS task
3. Deploy the new image to the web service and all 4 Celery services
4. Roll back the web service automatically if anything fails

---

## Prerequisites

Complete all steps in `DEPLOY_BABY_STEPS.md` first.
The workflow assumes the following already exist in AWS:
- ECR repository (`nizami/backend`)
- ECS cluster (`nizami-prod`)
- All ECS services: web, celery-extraction, celery-rag, celery-summaries, celery-beat
- A `nizami-migrate` task definition (see Step 17 in `DEPLOY_BABY_STEPS.md`)
- All secrets in Secrets Manager under `nizami/prod/*`

---

## Step 1 — Create the GitHub OIDC Provider in AWS IAM

OIDC lets GitHub Actions assume an AWS role without any long-lived access keys.
You create the trust once; GitHub's tokens do the authentication.

Run this once per AWS account (skip if it already exists):

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1 \
  --region me-south-1
```

Verify it was created:
```bash
aws iam list-open-id-connect-providers
```

You should see an entry with `token.actions.githubusercontent.com` in the URL.

---

## Step 2 — Create the IAM Role for GitHub Actions (Production)

This role is what the workflow assumes when it runs.
The trust policy restricts it to **only** the `main` branch of your repo.

Replace `YOUR_GITHUB_ORG` and `YOUR_REPO_NAME` before running.

```bash
aws iam create-role \
  --role-name nizami-github-actions-prod \
  --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect": "Allow",
        "Principal": {
          "Federated": "arn:aws:iam::ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com"
        },
        "Action": "sts:AssumeRoleWithWebIdentity",
        "Condition": {
          "StringEquals": {
            "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
          },
          "StringLike": {
            "token.actions.githubusercontent.com:sub": "repo:YOUR_GITHUB_ORG/YOUR_REPO_NAME:ref:refs/heads/main"
          }
        }
      }
    ]
  }'
```

The `StringLike` condition with `ref:refs/heads/main` means only a push to
`main` (or a `workflow_dispatch`) can assume this role. PRs from forks cannot.

Save the role ARN from the output — it looks like:
`arn:aws:iam::123456789:role/nizami-github-actions-prod`

---

## Step 3 — Attach Permissions to the Role

The role needs exactly the permissions required by the workflow — no more.

```bash
aws iam put-role-policy \
  --role-name nizami-github-actions-prod \
  --policy-name NizamiCICDPolicy \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [
      {
        "Sid": "ECRAuth",
        "Effect": "Allow",
        "Action": "ecr:GetAuthorizationToken",
        "Resource": "*"
      },
      {
        "Sid": "ECRPush",
        "Effect": "Allow",
        "Action": [
          "ecr:BatchCheckLayerAvailability",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:PutImage",
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer"
        ],
        "Resource": "arn:aws:ecr:me-south-1:ACCOUNT_ID:repository/nizami/backend"
      },
      {
        "Sid": "ECSRegisterTask",
        "Effect": "Allow",
        "Action": [
          "ecs:RegisterTaskDefinition",
          "ecs:DescribeTaskDefinition",
          "ecs:ListTaskDefinitions"
        ],
        "Resource": "*"
      },
      {
        "Sid": "ECSRunAndUpdate",
        "Effect": "Allow",
        "Action": [
          "ecs:RunTask",
          "ecs:StopTask",
          "ecs:DescribeTasks",
          "ecs:UpdateService",
          "ecs:DescribeServices"
        ],
        "Resource": "*",
        "Condition": {
          "ArnEquals": {
            "ecs:cluster": "arn:aws:ecs:me-south-1:ACCOUNT_ID:cluster/nizami-prod"
          }
        }
      },
      {
        "Sid": "PassRoleToECS",
        "Effect": "Allow",
        "Action": "iam:PassRole",
        "Resource": [
          "arn:aws:iam::ACCOUNT_ID:role/ecsTaskExecutionRole",
          "arn:aws:iam::ACCOUNT_ID:role/nizami-ecs-task-role"
        ]
      }
    ]
  }'
```

---

## Step 4 — Add the GitHub Secret

In your GitHub repo go to:
**Settings → Secrets and variables → Actions → Secrets → New repository secret**

| Name | Value |
|------|-------|
| `AWS_ROLE_ARN_PROD` | `arn:aws:iam::ACCOUNT_ID:role/nizami-github-actions-prod` |

This is the only secret the workflow needs. Everything else uses repository
variables (not secrets) because they are not sensitive.

---

## Step 5 — Add GitHub Repository Variables

In your GitHub repo go to:
**Settings → Secrets and variables → Actions → Variables → New repository variable**

Add each of the following. These are the real values from your AWS setup —
replace every placeholder with the actual ID from `DEPLOY_BABY_STEPS.md`.

| Variable | Example value | Where to find it |
|----------|---------------|------------------|
| `AWS_REGION` | `me-south-1` | Your chosen region |
| `ECR_BACKEND_REPO` | `123456789.dkr.ecr.me-south-1.amazonaws.com/nizami/backend` | ECR → nizami/backend → URI |
| `ECS_CLUSTER_PROD` | `nizami-prod` | ECS → Clusters |
| `ECS_WEB_SERVICE` | `nizami-web-service` | ECS → nizami-prod → Services |
| `ECS_WEB_TASK_FAMILY` | `nizami-web` | ECS → Task Definitions |
| `ECS_WEB_CONTAINER` | `web` | Container name in your task definition |
| `ECS_CELERY_EXTRACTION_SERVICE` | `nizami-celery-extraction-service` | ECS → nizami-prod → Services |
| `ECS_CELERY_EXTRACTION_FAMILY` | `nizami-celery-extraction` | ECS → Task Definitions |
| `ECS_CELERY_RAG_SERVICE` | `nizami-celery-rag-service` | ECS → nizami-prod → Services |
| `ECS_CELERY_RAG_FAMILY` | `nizami-celery-rag` | ECS → Task Definitions |
| `ECS_CELERY_SUMMARIES_SERVICE` | `nizami-celery-summaries-service` | ECS → nizami-prod → Services |
| `ECS_CELERY_SUMMARIES_FAMILY` | `nizami-celery-summaries` | ECS → Task Definitions |
| `ECS_CELERY_BEAT_SERVICE` | `nizami-celery-beat-service` | ECS → nizami-prod → Services |
| `ECS_CELERY_BEAT_FAMILY` | `nizami-celery-beat` | ECS → Task Definitions |
| `ECS_SUBNET_ID_PROD` | `subnet-0abc123...` | VPC → Subnets → nizami-private-a |
| `ECS_WEB_SG_PROD` | `sg-0be51edd...` | EC2 → Security Groups → web-sg |

> `ECS_SUBNET_ID_PROD` and `ECS_WEB_SG_PROD` are used only for the
> one-off migration task. Use a private subnet and the web security group.

---

## Step 6 — Create the GitHub Environment

The workflow uses `environment: production`, which lets you require approvals
before production deploys if you want.

In your GitHub repo go to:
**Settings → Environments → New environment → name it `production`**

Optional but recommended:
- Enable **Required reviewers** (you must approve before deploy runs)
- Set **Deployment branches** to `main` only

---

## How a Deploy Works (End to End)

```
Developer merges PR to main
    ↓
GitHub Actions triggers deploy-production.yml
    ↓
Assumes nizami-github-actions-prod IAM role via OIDC
    ↓
Logs into ECR
    ↓
Builds linux/amd64 Docker image tagged with the git SHA
    ↓
Pushes image to ECR
    ↓
Registers a new revision of nizami-migrate with the new image
    ↓
Runs nizami-migrate as a one-off Fargate task
    → Waits for it to stop
    → Checks exit code — aborts if non-zero
    ↓
Updates nizami-web task definition with new image → deploys → waits stable
    ↓
Updates nizami-celery-extraction → deploys → waits stable
    ↓
Updates nizami-celery-rag → deploys → waits stable
    ↓
Updates nizami-celery-summaries → deploys → waits stable
    ↓
Updates nizami-celery-beat → deploys → waits stable
    ↓
If anything fails: rolls back web service to the previous task definition
```

Total time: ~8–12 minutes (dominated by `wait-for-service-stability` on each service).

---

## Triggering a Manual Deploy

You can trigger a deploy without pushing to `main`:

1. Go to **Actions → Deploy Production → Run workflow**
2. Optionally enter a specific git SHA to re-deploy an older image
3. Click **Run workflow**

Useful for rolling back: enter the git SHA of the last known-good commit.

---

## Monitoring a Deploy

Watch it live in **GitHub Actions → Deploy Production → (latest run)**.

Each step streams logs in real time. If a step fails:

- **Migration failed**: check CloudWatch Logs → `/ecs/nizami/web` (prefix: `migrate`)
- **Service not stable**: check CloudWatch Logs → `/ecs/nizami/web` and ECS → service events
- **Image pull failed**: check that the ECR repo URI in `ECR_BACKEND_REPO` is correct

```bash
# Tail web logs directly from your terminal
aws logs tail /ecs/nizami/web --follow --region me-south-1

# Check why a service is not stable
aws ecs describe-services \
  --cluster nizami-prod \
  --services nizami-web-service \
  --query 'services[0].events[:5]'
```
