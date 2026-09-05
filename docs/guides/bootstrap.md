# Bootstrap guide

One-time setup (NFR-7) a stranger with an AWS account runs before anything else in this repo
works. Run once per AWS account; `infra/main` and every later phase depend on its output.

## Prerequisites

- An AWS account and an IAM identity with enough privilege to create IAM roles/policies, an S3
  bucket, a DynamoDB table, an OIDC provider, and AWS Budgets resources (an admin-ish bootstrap
  identity — this is the one place in the project a human credential is expected).
- Terraform >= 1.9, AWS CLI v2.
- Admin access to the `noahwins-ng/hyperlake` GitHub repo (to set one repo variable).

## Steps

1. **Apply the bootstrap stack** (local state — this is what creates the S3 backend, so it can't
   use one itself):
   ```
   cd infra/bootstrap
   terraform init
   terraform apply
   ```
   You'll be prompted for `budget_notification_email` — the address that receives the $10 alarm
   and the $15 deny-action notice. It is never committed to the repo.

   AC1 proof: a second `terraform plan` immediately after should report no changes.

2. **Wire the S3 backend for `infra/main`:**
   ```
   terraform output -raw state_bucket
   terraform output -raw state_lock_table
   ```
   Copy `infra/main/backend.hcl.example` to `infra/main/backend.hcl` (gitignored — the bucket
   name embeds your account ID) and fill in those two values.
   ```
   cd ../main
   terraform init -backend-config=backend.hcl
   terraform plan
   ```
   AC2 proof: the plan output names the S3 backend and lock table; no `terraform.tfstate` file
   appears locally.

3. **Set the GitHub Actions OIDC role as a repo variable** (not a secret — the role ARN isn't
   sensitive; the trust policy is what protects it):
   ```
   terraform -chdir=../bootstrap output -raw github_actions_role_arn
   gh variable set AWS_OIDC_ROLE_ARN --body "<paste the ARN>"
   ```

4. **Prove OIDC works with no stored key** — run the `verify-oidc` workflow:
   ```
   gh workflow run verify-oidc.yml
   gh run watch
   ```
   AC3 proof: the run's `aws sts get-caller-identity` step succeeds; no long-lived AWS access key
   exists anywhere in the repo (`ci.yml`'s own check enforces this).

5. **Cost allocation tag activation (up to 24h).** `terraform apply` in step 1 also tries to
   activate `project` as a cost allocation tag. AWS only lists a tag as activatable once it has
   appeared in billing data — on the very first apply this can fail because the tag hasn't
   propagated yet. If so, re-run `terraform apply` in `infra/bootstrap` the next day. Check with:
   ```
   aws ce list-cost-allocation-tags --status Active
   ```
   AC5 proof: `project` shows `Active` in that output.

## What the $15 budget action does — and doesn't do

The budget action at $15 denies the GitHub Actions OIDC role from *creating* new Kinesis
streams, ECS services/tasks, or Lambda functions. It never deletes or stops anything already
running — Budgets data lags actual spend by 8–12h, so acting on it destructively would be acting
on stale information. Stopping an already-running session is `session-down`'s and the session
reaper's job (NFR-1), not the budget action's.
