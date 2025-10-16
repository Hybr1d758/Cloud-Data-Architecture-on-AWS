# Cloud Data Architecture on AWS

## Project Purpose
Demonstrate end-to-end data platform engineering skills across compute, storage, and analytics on AWS by delivering a reference architecture, implementation backlog, and collaboration workflow for a fictional analytics program.

## Business Scenario
Northwind Trading is modernizing its analytics stack to support near-real-time decision making for marketing and supply chain teams. The company ingests transactional order data, product catalogs, clickstream events, and third-party market indicators. Stakeholders need consolidated insights with strict security and operational guardrails.

## Stakeholder Personas
- **Product Owner (Data Platform Lead)**: defines roadmap, prioritizes backlog, approves releases.
- **Data Engineers**: implement ingestion/ETL pipelines, maintain data quality, coordinate with platform team.
- **Analytics Engineers**: shape curated datasets, manage semantic models, ensure dashboards meet business SLAs.
- **Application Engineers**: deploy workloads to EC2 that consume curated data and publish APIs.
- **Data Governance & Security**: enforce compliance, IAM policies, data classifications, retention.
- **DevOps/SRE**: manage infrastructure automation, monitoring, incident response.

## Project Architect
- **Edward Junior Effah-Nyarko** – AWS Data Architect (UK). Leads platform strategy, infrastructure automation, and cross-team collaboration for this reference implementation. Oversees VPC/network design, data lake governance, ETL standards, and stakeholder alignment.

## Functional Requirements
- Ingest batch and incremental datasets into `aws_s3` data lake landing zones.
- Transform and curate datasets via Python/Spark/AWS Glue jobs with quality checks.
- Serve aggregated, relational data sets in `aws_rds` for reporting applications.
- Provide high-availability compute tier on `aws_ec2` for analytics microservices.
- Support cross-domain collaboration with version control, ticketing, and peer review.
- Expose observability for pipelines and infrastructure (CloudWatch, Glue job metrics, RDS performance insights).

## Non-Functional Requirements
- Enforce IAM least privilege and data encryption at rest/in transit.
- Support dev/stage/prod environment separation and CI/CD promotion gates.
- Achieve 99.5% pipeline availability with automated retries and alerting.
- Ensure infrastructure definitions are reproducible via Python-based automation (boto3/CDK).
- Align backup/restore strategy for RDS and critical S3 buckets (versioning, lifecycle rules).

## Success Metrics
- < 30 minutes to deploy a new environment using Python automation scripts and pipeline tooling.
- 95% of source datasets delivered to curated layer within SLA windows.
- P0/P1 incident MTTR under 2 hours with documented runbooks.
- ≥ 80% test coverage for critical ETL transformation logic.

## Data Sources & Classification
- **Transactional Orders** (internal, confidential, PII) → nightly batch via secure transfer.
- **Product Catalog** (internal, restricted) → weekly extracts through API.
- **Clickstream Events** (semi-structured JSON) → streaming-ready, staged hourly.
- **Market Indicators** (third-party, licensed) → CSV via vendor SFTP.
- **Vehicle Market Valuation API** (external, licensed) → REST integration via RapidAPI endpoint `https://vehicle-market-value.p.rapidapi.com/vmv?license_plate=<plate>&state_code=<state>` secured with API key and rate-limit safeguards; used for enrichment use cases.

### External API Integration Notes
- **Endpoint**: `GET https://vehicle-market-value.p.rapidapi.com/vmv`
- **Query Parameters**: `license_plate`, `state_code` (2-letter state abbreviation).
- **Headers**:
  - `x-rapidapi-host: vehicle-market-value.p.rapidapi.com`
  - `x-rapidapi-key: <securely stored secret>`
- **Usage**: Fetch blue-book style valuations to enrich asset data. Responses cached in S3 raw zone; sensitive keys managed via AWS Secrets Manager and injected into Glue/ETL jobs at runtime.
- **Governance Considerations**: Track API consumption costs, enforce retries/backoff, and log requests for audit.

## High-Level Architecture Overview
1. **Landing / Raw Zone (S3)**: Source files ingested into `s3://northwind-data-lake/raw/<source>/date=` with server-side encryption, object versioning, and event notifications.
2. **Data Processing (AWS Glue / PySpark on AWS Glue or EMR)**: Jobs orchestrated via AWS Glue Workflows or Step Functions, storing metadata in AWS Glue Data Catalog.
3. **Curated Zone (S3)**: Partitioned Parquet datasets for analytics teams; schema enforced, quality checks logged.
4. **Serving Layer (RDS PostgreSQL)**: Aggregated tables optimized for BI tools and EC2-hosted services.
5. **Compute Workloads (EC2 Auto Scaling Group)**: Containerized or AMI-based workloads consuming curated datasets and exposing APIs/reporting.
6. **Observability & Governance**: CloudWatch dashboards, AWS Config, AWS Lake Formation policies, Security Hub alerts.

## Collaboration & Workflow
- **Backlog Management**: Use GitHub Projects or Jira. User stories follow INVEST criteria, each with definition of done and data quality acceptance tests.
- **Version Control**: Git flow with feature branches, PR reviews by Data Engineering + DevOps. All Python infrastructure automation and ETL code resides in mono-repo with directory ownership defined.
- **Change Management**: CI pipelines require linting, unit tests, and static analysis before merge. Infrastructure change simulations/dry runs reviewed and approved by platform lead before applying.
- **Documentation**: Architecture decisions recorded in ADRs within `docs/adr`. Runbooks and onboarding guides live in `docs/runbooks`.
- **Cross-Team Ceremonies**: Weekly sync across platform, analytics, and governance teams; monthly architecture review; incident postmortems scheduled within 48 hours of closure.

## Implementation Backlog (Phase 1 Deliverables)
1. Draft Python infrastructure automation skeletons (boto3/CDK) for VPC, EC2, RDS, S3 (skeleton only, detailed in Phase 2).
2. Produce architecture diagram (PlantUML or draw.io) stored in `docs/architecture`.
3. Define data contracts for each source with schema expectations and validation rules.
4. Author runbook outline covering incident triage, on-call rotations, escalation contacts.
5. Establish coding standards for ETL scripts (naming, logging, testing strategy).
6. Set up CI pipeline stub (GitHub Actions or CodePipeline) to run Python lint + unit test placeholders.
7. Document RapidAPI vehicle valuation integration (auth flow, rate limits, contract tests) within ETL guidelines.

## Open Questions
- Confirm regulatory requirements (GDPR, CCPA) impacting data retention policies.
- Determine data freshness targets for each consumer persona.
- Decide whether to leverage serverless analytics (Athena) in addition to RDS for ad-hoc queries.

## Next Steps
- Finalize stakeholder approvals of requirements and architecture.
- Begin detailed Python infrastructure automation design (Phase 2) and create infrastructure skeletons.
- Collect sample datasets and define transformation use cases for Phase 4 prototypes.

