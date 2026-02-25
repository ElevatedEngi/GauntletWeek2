# Monitoring & Alerting Guide

> **HIPAA Note:** All monitoring data, dashboards, and alert messages must be
> free of PHI. Use patient PIDs (numeric) only — never names, DOBs, or MRNs.

---

## Overview

The Chart Summarizer Agent exposes structured JSON logs and a `/api/v1/health`
endpoint for monitoring. Integrate with your preferred observability stack using
the patterns below.

---

## 1. Health Endpoint

**`GET /api/v1/health`** — no authentication required.

```json
{
  "status": "ok",
  "version": "0.1.0",
  "uptime_seconds": 3600,
  "llm_provider": "anthropic",
  "fhir_connected": true
}
```

Configure your load balancer or container orchestrator to poll this endpoint
every 30 seconds. Alert if `status != "ok"` or if the endpoint is unreachable.

---

## 2. Structured Log Format

All log lines are JSON-structured with these common fields:

| Field         | Description                              |
|---------------|------------------------------------------|
| `timestamp`   | ISO-8601 UTC                             |
| `level`       | DEBUG / INFO / WARNING / ERROR           |
| `logger`      | Module path (e.g. `chart_summarizer.api.routes`) |
| `message`     | Human-readable description               |
| `request_id`  | UUID, correlates request → response logs |
| `user_id`     | Authenticated user ID (never PHI)        |

**Key log patterns to watch:**

| Pattern | Level | Meaning |
|---------|-------|---------|
| `AUDIT_WRITE_FAILED` | ERROR | DB audit insert failed — investigate DB health |
| `Summary generation failed` | ERROR | LLM call or pipeline error |
| `Rate limit exceeded` | INFO | Normal; excessive frequency may indicate abuse |
| `Database initialisation failed` | ERROR | App started but audit log may be broken |

---

## 3. AWS CloudWatch Integration

### Log Aggregation

Use the CloudWatch Logs driver to forward container logs:

```yaml
# docker-compose.prod.yml — add to agent service
logging:
  driver: awslogs
  options:
    awslogs-group: /openemr/chart-summarizer
    awslogs-region: us-east-1
    awslogs-stream: agent-{container-id}
```

### Metric Filters

Create CloudWatch Metric Filters on the log group:

```bash
# Error rate metric
aws logs put-metric-filter \
  --log-group-name /openemr/chart-summarizer \
  --filter-name ErrorRate \
  --filter-pattern '[ts, level="ERROR", ...]' \
  --metric-transformations \
    metricName=ErrorCount,metricNamespace=ChartSummarizer,metricValue=1

# LLM failure metric
aws logs put-metric-filter \
  --log-group-name /openemr/chart-summarizer \
  --filter-name LLMFailure \
  --filter-pattern '"Summary generation failed"' \
  --metric-transformations \
    metricName=LLMFailures,metricNamespace=ChartSummarizer,metricValue=1
```

### CloudWatch Alarms

```bash
# 1. Error rate > 5% over 5 minutes
aws cloudwatch put-metric-alarm \
  --alarm-name ChartSummarizer-HighErrorRate \
  --alarm-description "Error rate exceeds 5% over 5 minutes" \
  --metric-name ErrorCount \
  --namespace ChartSummarizer \
  --statistic Sum \
  --period 300 \
  --threshold 5 \
  --comparison-operator GreaterThanThreshold \
  --alarm-actions arn:aws:sns:us-east-1:ACCOUNT_ID:ops-alerts \
  --evaluation-periods 1

# 2. LLM API failures
aws cloudwatch put-metric-alarm \
  --alarm-name ChartSummarizer-LLMFailures \
  --alarm-description "LLM API failures detected" \
  --metric-name LLMFailures \
  --namespace ChartSummarizer \
  --statistic Sum \
  --period 60 \
  --threshold 3 \
  --comparison-operator GreaterThanThreshold \
  --alarm-actions arn:aws:sns:us-east-1:ACCOUNT_ID:ops-alerts \
  --evaluation-periods 1
```

### P95 Latency Alert (from access logs)

If fronted by an ALB, create an alarm on `TargetResponseTime` P95:

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name ChartSummarizer-HighLatency \
  --alarm-description "P95 response time > 30 seconds" \
  --metric-name TargetResponseTime \
  --namespace AWS/ApplicationELB \
  --statistic p95 \
  --extended-statistic p95 \
  --period 300 \
  --threshold 30 \
  --comparison-operator GreaterThanThreshold \
  --alarm-actions arn:aws:sns:us-east-1:ACCOUNT_ID:ops-alerts
```

---

## 4. LangSmith LLM Observability

[LangSmith](https://smith.langchain.com/) provides trace-level LLM observability.

### Setup

```bash
# Add to .env.prod
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=ls__your_langsmith_key
LANGCHAIN_PROJECT=openemr-chart-summarizer-prod
LANGCHAIN_ENDPOINT=https://api.smith.langchain.com
```

### What Gets Traced

With `LANGCHAIN_TRACING_V2=true`, LangSmith automatically captures:
- Every LLM call (prompt, completion, latency, token counts)
- LangGraph node execution order
- Tool call inputs/outputs (FHIR API responses)

### HIPAA Warning

> **Ensure HIPAA BAA is in place with LangSmith before enabling tracing in
> production.** Review LangSmith's data retention settings. Consider disabling
> tracing for production and only enabling for staging/debug.

To exclude PHI from traces, set `HIPAA_MODE=true` — the pipeline will redact
patient identifiers before they reach the LangSmith trace.

---

## 5. Key Metrics to Monitor

| Metric | Alert Threshold | Action |
|--------|-----------------|--------|
| Error rate | > 5% over 5 minutes | Page on-call; check LLM API status |
| P95 latency | > 30 seconds | Investigate LLM timeouts; scale if needed |
| LLM API failures | > 3 in 1 minute | Check Anthropic/OpenAI status page |
| Monthly LLM cost | > 80% of budget | Review usage; consider rate limit reduction |
| DB audit write failures | Any | Check database connectivity |
| Rate limit hits | Spike > 10x baseline | Possible abuse; review user activity |
| Container health | Unhealthy > 2 retries | Restart policy fires; investigate logs |

---

## 6. Monthly Cost Monitoring

Track token usage from the audit log database:

```sql
-- Monthly cost estimate (requires cost_estimate column in audit_log)
SELECT
    DATE_FORMAT(timestamp, '%Y-%m') AS month,
    COUNT(*)                        AS total_requests,
    SUM(token_count)                AS total_tokens,
    ROUND(SUM(cost_estimate), 2)    AS estimated_usd
FROM audit_log
WHERE outcome = 'success'
GROUP BY month
ORDER BY month DESC;
```

Set a CloudWatch Billing Alarm at 80% of your monthly LLM API budget:

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name LLMCostApproachingBudget \
  --metric-name EstimatedCharges \
  --namespace AWS/Billing \
  --statistic Maximum \
  --period 86400 \
  --threshold YOUR_80_PERCENT_BUDGET \
  --comparison-operator GreaterThanThreshold \
  --alarm-actions arn:aws:sns:us-east-1:ACCOUNT_ID:ops-alerts \
  --dimensions Name=ServiceName,Value=AmazonBedrock
```

---

## 7. Dashboard Checklist

Build a monitoring dashboard with these panels:

- [ ] Request rate (req/min) by status code
- [ ] P50 / P95 / P99 latency trend
- [ ] Error rate % (rolling 5-min window)
- [ ] LLM token consumption (daily/weekly)
- [ ] Estimated LLM cost (daily/monthly)
- [ ] Rate limit events per user
- [ ] Confidence level distribution (GREEN/YELLOW/RED ratio)
- [ ] Container health check status
- [ ] Active audit log entries (INSERT rate)