# HIPAA Deployment Checklist

This document describes the required steps to deploy the Chart Summarizer Agent
in a HIPAA-compliant manner. Complete all items before handling real patient data.

---

## Pre-Deployment Requirements

### 1. Business Associate Agreements (BAA)

- [ ] Execute HIPAA BAA with **Anthropic** (primary LLM provider)
  - Available via Anthropic enterprise agreement
  - Confirm zero-data-retention policy is enabled
- [ ] Execute HIPAA BAA with **OpenAI** (fallback LLM provider)
  - Available via OpenAI enterprise/API agreement
- [ ] Execute HIPAA BAA with **AWS** (hosting and Secrets Manager)
  - Available via AWS standard BAA
- [ ] Execute HIPAA BAA with **LangSmith** (observability)
  - Enable PHI-scrubbing in LangSmith project settings for production traces

---

### 2. Encryption Configuration

#### In Transit
- [ ] TLS 1.2+ enforced on all connections (load balancer, inter-service)
- [ ] No PHI in URL path segments or query strings
- [ ] HTTPS-only; HTTP redirects to HTTPS at load balancer level

#### At Rest
- [ ] AES-256-GCM encryption enabled for `chart_summarizer_summaries.summary_text`
- [ ] Encryption key stored in **AWS Secrets Manager** (never in code or `.env`)
- [ ] MySQL data directory encrypted (AWS RDS encryption-at-rest enabled)
- [ ] EBS volumes for EC2/Fargate encrypted

---

### 3. API Key Management

- [ ] Anthropic API key stored in AWS Secrets Manager, not in environment variables
- [ ] OpenAI API key stored in AWS Secrets Manager
- [ ] OpenEMR OAuth2 client secret stored in AWS Secrets Manager
- [ ] Key rotation schedule set to 90 days
- [ ] IAM role for ECS task has `secretsmanager:GetSecretValue` permission only for specific secret ARNs

---

### 4. Network Configuration

- [ ] Python microservice deployed in **private subnet** (no direct internet access)
- [ ] OpenEMR and agent service in the **same VPC**
- [ ] Security group rules: agent only accepts inbound from OpenEMR security group
- [ ] No PHI accessible from public internet
- [ ] VPC Flow Logs enabled for the relevant VPC

---

### 5. Logging and Audit

- [ ] Set `HIPAA_MODE=true` in production
- [ ] Verify `AUDIT_LOG_ENABLED=true`
- [ ] Confirm no patient names, DOBs, or SSNs appear in application logs
  - Run test: trigger a summary for a synthetic patient and grep logs for PHI
- [ ] Audit logs retained per practice data retention policy (default: 7 years)
- [ ] CloudWatch log group with appropriate retention policy configured
- [ ] Log group encrypted with KMS key

---

### 6. LangSmith (Observability)

- [ ] Set LangSmith project to **production mode**
- [ ] Disable raw prompt/completion logging (contains patient data summaries)
- [ ] Use anonymised patient IDs (hashed PID) in trace metadata
- [ ] Confirm LangSmith BAA is in place before enabling production traces

---

### 7. Access Control

- [ ] OpenEMR ACL configured: only providers with chart read access can request summaries
- [ ] Admin-only access to the module settings page
- [ ] Max 20 summaries per user per hour (rate limiting configured)
- [ ] Service account (OpenEMR OAuth client) has minimum required FHIR scopes

---

### 8. Container Security

- [ ] Docker image runs as **non-root user** (`app` user, UID 1000)
- [ ] No secrets in Docker image layers (use Secrets Manager at runtime)
- [ ] Container image scanned for vulnerabilities (ECR image scanning enabled)
- [ ] Read-only root filesystem where possible

---

## Environment Variables for Production

Set these in AWS Secrets Manager and inject via ECS task definition secrets:

```bash
LLM_PROVIDER=anthropic
LLM_MODEL=claude-haiku-4-5-20251001
LLM_API_KEY=<from Secrets Manager>
OPENEMR_FHIR_BASE_URL=http://openemr.internal/fhir
OPENEMR_CLIENT_ID=<from Secrets Manager>
OPENEMR_CLIENT_SECRET=<from Secrets Manager>
LOG_LEVEL=INFO
AUDIT_LOG_ENABLED=true
HIPAA_MODE=true
MAX_CONCURRENT_REQUESTS=10
SUMMARY_DEFAULT_MONTHS=12
MAX_ENCOUNTERS_PER_SUMMARY=50
```

**Never set `HIPAA_MODE=false` in production.**

---

## Incident Response

| Scenario | Response |
|----------|----------|
| PHI appears in logs | Immediately rotate affected keys; audit log access; notify privacy officer |
| LLM provider data breach | Invoke BAA breach notification process; assess PHI exposure scope |
| Unauthorized summary access | Revoke OAuth tokens; audit `chart_summarizer_requests` table; notify patients |
| Hallucinated clinical fact causes harm | Preserve all logs and LangSmith traces; engage legal; disable module pending review |

---

## Compliance References

- HIPAA Security Rule: 45 CFR §§ 164.302–164.318
- ONC Decision Support Interventions (DSI) B11 criteria
- OpenEMR HIPAA compliance documentation: https://www.open-emr.org/wiki/index.php/HIPAA
