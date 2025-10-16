"""Vehicle market value ingestion and enrichment pipeline.

Fetches vehicle valuation data from RapidAPI, persists raw payloads into S3,
transforms into curated schema, and loads aggregates into RDS.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List

import boto3
import requests
import yaml
from botocore.exceptions import ClientError

LOGGER = logging.getLogger(__name__)


@dataclass
class PipelineConfig:
    environment: str
    region: str
    endpoint: str
    secret_name: str
    max_attempts: int
    backoff_seconds: int
    default_state: str
    plates: List[str]
    raw_bucket: str
    curated_bucket: str
    raw_prefix: str
    curated_prefix: str
    rds_endpoint: str
    rds_database: str
    rds_table: str
    rds_secret: str
    rds_username_key: str
    rds_password_key: str


def load_config(path: str = "etl/config/base.yaml") -> PipelineConfig:
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    run = data["run"]
    source = data["sources"]["vehicle_market_value"]
    storage = data["storage"]
    rds = data["rds"]
    return PipelineConfig(
        environment=run["environment"],
        region=run["region"],
        endpoint=source["endpoint"],
        secret_name=source["secret_name"],
        max_attempts=source["retries"]["max_attempts"],
        backoff_seconds=source["retries"]["backoff_seconds"],
        default_state=source["default_state"],
        plates=source["plates"],
        raw_bucket=storage["raw_bucket"],
        curated_bucket=storage["curated_bucket"],
        raw_prefix=storage["prefix"]["raw"],
        curated_prefix=storage["prefix"]["curated"],
        rds_endpoint=rds["cluster_endpoint"],
        rds_database=rds["database"],
        rds_table=rds["table"],
        rds_secret=rds["secret_name"],
        rds_username_key=rds["username_key"],
        rds_password_key=rds["password_key"],
    )


class VehicleMarketPipeline:
    def __init__(self, config: PipelineConfig):
        session = boto3.Session(region_name=config.region)
        self.secrets = session.client("secretsmanager")
        self.s3 = session.client("s3")
        self.config = config

    def run(self) -> None:
        LOGGER.info("Starting vehicle market value pipeline")
        api_key = self._get_secret(self.config.secret_name)
        records = []
        for plate in self.config.plates:
            payload = self._fetch_vehicle_value(api_key, plate, self.config.default_state)
            records.append(self._normalize_payload(plate, payload))
            self._write_raw(plate, payload)
        curated = self._to_curated(records)
        self._write_curated(curated)
        LOGGER.info("Pipeline completed with %d records", len(curated))

    def _get_secret(self, name: str) -> str:
        response = self.secrets.get_secret_value(SecretId=name)
        secret_string = response.get("SecretString")
        if not secret_string:
            raise ValueError(f"Secret {name} missing SecretString")
        return json.loads(secret_string)["x-rapidapi-key"]

    def _fetch_vehicle_value(self, api_key: str, plate: str, state: str) -> Dict[str, Any]:
        headers = {
            "x-rapidapi-host": "vehicle-market-value.p.rapidapi.com",
            "x-rapidapi-key": api_key,
        }
        params = {"license_plate": plate, "state_code": state}
        response = requests.get(self.config.endpoint, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        return response.json()

    def _write_raw(self, plate: str, payload: Dict[str, Any]) -> None:
        key = f"{self.config.raw_prefix}/{plate}/{datetime.now(timezone.utc).isoformat()}.json"
        self.s3.put_object(Bucket=self.config.raw_bucket, Key=key, Body=json.dumps(payload))

    def _normalize_payload(self, plate: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        valuation = payload.get("valuation", {})
        return {
            "plate": plate,
            "year": payload.get("year"),
            "make": payload.get("make"),
            "model": payload.get("model"),
            "trim": payload.get("trim"),
            "mileage": payload.get("mileage"),
            "condition": payload.get("condition"),
            "retail_value": valuation.get("retail"),
            "wholesale_value": valuation.get("wholesale"),
            "trade_in_value": valuation.get("trade_in"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "vehicle_market_value_api",
        }

    def _to_curated(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return records

    def _write_curated(self, records: List[Dict[str, Any]]) -> None:
        key = f"{self.config.curated_prefix}/{datetime.now(timezone.utc).strftime('%Y/%m/%d')}/vehicles.json"
        body = json.dumps(records)
        self.s3.put_object(Bucket=self.config.curated_bucket, Key=key, Body=body)


def main(config_path: str = "etl/config/base.yaml") -> None:
    logging.basicConfig(level=logging.INFO)
    config = load_config(config_path)
    pipeline = VehicleMarketPipeline(config)
    pipeline.run()


if __name__ == "__main__":
    main()

