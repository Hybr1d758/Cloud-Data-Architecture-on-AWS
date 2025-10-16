# Vehicle Market Value Pipeline Runbook

## Purpose
Ingest vehicle valuation data from the RapidAPI Vehicle Market Value endpoint, land raw payloads in S3, transform to curated schema, and publish aggregates into the analytics serving layer (RDS).

## Trigger & Schedule
- **Schedule**: Hourly cron in Step Functions or Airflow (placeholder `0 * * * *`).
- **Manual Trigger**: `python etl/pipelines/vehicle_market_value.py --config etl/config/base.yaml`

## Pre-requisites
- AWS credentials with access to S3, Secrets Manager, RDS, and CloudWatch.
- Secrets Manager entries:
  - `rapidapi/vehicle-market-value` with `x-rapidapi-key`.
  - `rds/northwind-analytics` containing `username` and `password`.
- S3 buckets `northwind-data-raw-eu-north-1-dev` and `northwind-data-curated-eu-north-1-dev` exist with versioning and encryption.

## Run Steps
1. Activate Python virtual environment and install dependencies: `pip install -r etl/requirements.txt`.
2. Export `AWS_REGION=eu-north-1` and set AWS credentials.
3. Execute pipeline `python etl/pipelines/vehicle_market_value.py`.
4. Verify CloudWatch logs for pipeline execution.
5. Confirm S3 objects written under `vehicle_market/raw/` and `vehicle_market/curated/` prefixes.
6. Validate RDS table `vehicle_market_valuation` receives new rows (future enhancement).

## Monitoring & Alerts
- CloudWatch metric filters on log group for errors.
- S3 event notifications to detect missing or late-arriving data.
- Future: RDS performance insights and data quality dashboards.

## Rollback
- Data: remove erroneous curated objects using S3 object versioning; reprocess from raw zone.
- Infrastructure: revert to previous Git commit and redeploy.

## Troubleshooting
- **API errors**: check RapidAPI key rotation, rate limits. Pipeline retries up to 5 times with exponential backoff.
- **Secrets retrieval failed**: ensure IAM role has `secretsmanager:GetSecretValue` on required secrets.
- **S3 write failures**: confirm bucket policies and encryption settings. Use `aws s3api head-object` to validate path.
- **Data drift**: review schema changes via data quality checks (stubbed; extend with Great Expectations).

## Ownership
- **Data Engineering Lead**: primary on-call.
- **Platform/SRE**: supports infrastructure automation.
- **Analytics Consumer**: notifies of data quality issues.

