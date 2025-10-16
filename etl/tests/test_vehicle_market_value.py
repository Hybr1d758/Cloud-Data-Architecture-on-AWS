import json
from unittest import mock

import pytest

from etl.pipelines.vehicle_market_value import VehicleMarketPipeline, PipelineConfig


@pytest.fixture
def config() -> PipelineConfig:
    return PipelineConfig(
        environment="test",
        region="eu-north-1",
        endpoint="https://vehicle-market-value.p.rapidapi.com/vmv",
        secret_name="test/secret",
        max_attempts=3,
        backoff_seconds=1,
        default_state="AL",
        plates=["S8TAN"],
        raw_bucket="raw",
        curated_bucket="curated",
        raw_prefix="raw_prefix",
        curated_prefix="curated_prefix",
        rds_endpoint="endpoint",
        rds_database="analytics",
        rds_table="vehicle_market_valuation",
        rds_secret="rds/secret",
        rds_username_key="username",
        rds_password_key="password",
    )


@mock.patch("etl.pipelines.vehicle_market_value.requests.get")
@mock.patch("etl.pipelines.vehicle_market_value.boto3.Session")
def test_pipeline_run(mock_session, mock_get, config):
    secrets_client = mock.Mock()
    secrets_client.get_secret_value.return_value = {"SecretString": json.dumps({"x-rapidapi-key": "key"})}
    s3_client = mock.Mock()
    session_instance = mock.Mock()
    session_instance.client.side_effect = [secrets_client, s3_client]
    mock_session.return_value = session_instance

    mock_get.return_value.json.return_value = {
        "year": 2020,
        "make": "Tesla",
        "model": "Model 3",
        "valuation": {"retail": 50000},
    }
    mock_get.return_value.raise_for_status.return_value = None

    pipeline = VehicleMarketPipeline(config)
    pipeline.run()

    mock_get.assert_called_once()
    assert s3_client.put_object.call_count == 2

