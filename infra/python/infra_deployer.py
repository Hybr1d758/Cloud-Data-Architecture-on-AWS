"""Infrastructure deployment utilities for AWS using boto3.

This module creates foundational resources (VPC, subnets, EC2 ASG, RDS, S3)
based on declarative configuration. All secrets must be provided through
environment variables or AWS Secrets Manager—never hardcode credentials.

Guardrails applied:
- Idempotent create-or-update patterns using tag lookups.
- Separation of configuration from code via YAML.
- Built-in retries and exponential backoff for API calls.
"""

from __future__ import annotations

import base64
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import boto3
import botocore
import yaml
from botocore.exceptions import ClientError
from rich.console import Console
from rich.table import Table

LOGGER = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

console = Console()


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def retryable(func):
    def wrapper(*args, **kwargs):
        attempts = kwargs.pop("max_attempts", 5)
        delay = kwargs.pop("backoff_seconds", 2)
        for attempt in range(1, attempts + 1):
            try:
                return func(*args, **kwargs)
            except ClientError as exc:
                if attempt == attempts:
                    raise
                LOGGER.warning("%s failed (attempt %d/%d): %s", func.__name__, attempt, attempts, exc)
                time.sleep(delay * attempt)
    return wrapper


@dataclass
class AWSClients:
    ec2: Any
    autoscaling: Any
    rds: Any
    s3: Any
    iam: Any
    secretsmanager: Any


def init_clients(region: str) -> AWSClients:
    session = boto3.Session(region_name=region)
    return AWSClients(
        ec2=session.client("ec2"),
        autoscaling=session.client("autoscaling"),
        rds=session.client("rds"),
        s3=session.client("s3"),
        iam=session.client("iam"),
        secretsmanager=session.client("secretsmanager"),
    )


class VPCManager:
    def __init__(self, ec2):
        self.ec2 = ec2

    @retryable
    def ensure_vpc(self, name: str, cidr: str, tags: Dict[str, str]) -> str:
        existing = self.ec2.describe_vpcs(Filters=[{"Name": "tag:Name", "Values": [name]}]).get("Vpcs")
        if existing:
            vpc_id = existing[0]["VpcId"]
            LOGGER.info("Reusing VPC %s", vpc_id)
            return vpc_id
        response = self.ec2.create_vpc(CidrBlock=cidr, TagSpecifications=[{"ResourceType": "vpc", "Tags": _tags(name, tags)}])
        vpc_id = response["Vpc"]["VpcId"]
        self.ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={"Value": True})
        self.ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={"Value": True})
        return vpc_id

    @retryable
    def ensure_internet_gateway(self, vpc_id: str, name: str, tags: Dict[str, str]) -> str:
        igws = self.ec2.describe_internet_gateways(Filters=[{"Name": "attachment.vpc-id", "Values": [vpc_id]}]).get("InternetGateways")
        if igws:
            return igws[0]["InternetGatewayId"]
        igw = self.ec2.create_internet_gateway(TagSpecifications=[{"ResourceType": "internet-gateway", "Tags": _tags(name, tags)}])
        igw_id = igw["InternetGateway"]["InternetGatewayId"]
        self.ec2.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
        return igw_id

    @retryable
    def ensure_subnet(self, name: str, vpc_id: str, cidr: str, az: str, public: bool, tags: Dict[str, str]) -> str:
        existing = self.ec2.describe_subnets(Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "cidr-block", "Values": [cidr]}
        ]).get("Subnets")
        if existing:
            return existing[0]["SubnetId"]
        subnet = self.ec2.create_subnet(
            VpcId=vpc_id,
            CidrBlock=cidr,
            AvailabilityZone=az,
            TagSpecifications=[{"ResourceType": "subnet", "Tags": _tags(name, tags)}],
        )
        subnet_id = subnet["Subnet"]["SubnetId"]
        if public:
            self.ec2.modify_subnet_attribute(SubnetId=subnet_id, MapPublicIpOnLaunch={"Value": True})
        return subnet_id


class RouteTableManager:
    def __init__(self, ec2):
        self.ec2 = ec2

    @retryable
    def ensure_route_table(self, vpc_id: str, name: str, tags: Dict[str, str]) -> str:
        existing = self.ec2.describe_route_tables(Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "tag:Name", "Values": [name]},
        ]).get("RouteTables")
        if existing:
            return existing[0]["RouteTableId"]
        response = self.ec2.create_route_table(
            VpcId=vpc_id,
            TagSpecifications=[{"ResourceType": "route-table", "Tags": _tags(name, tags)}],
        )
        return response["RouteTable"]["RouteTableId"]

    @retryable
    def ensure_route(self, route_table_id: str, destination_cidr: str, gateway_id: Optional[str] = None, nat_gateway_id: Optional[str] = None) -> None:
        params = {
            "RouteTableId": route_table_id,
            "DestinationCidrBlock": destination_cidr,
        }
        if gateway_id:
            params["GatewayId"] = gateway_id
        if nat_gateway_id:
            params["NatGatewayId"] = nat_gateway_id
        try:
            self.ec2.create_route(**params)
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "RouteAlreadyExists":
                raise

    @retryable
    def associate(self, route_table_id: str, subnet_id: str) -> None:
        self.ec2.associate_route_table(RouteTableId=route_table_id, SubnetId=subnet_id)


class NATGatewayManager:
    def __init__(self, ec2):
        self.ec2 = ec2

    @retryable
    def ensure_nat_gateway(self, subnet_id: str, name: str, tags: Dict[str, str]) -> str:
        eip = self.ec2.allocate_address(Domain="vpc", TagSpecifications=[{"ResourceType": "elastic-ip", "Tags": _tags(name, tags)}])
        nat = self.ec2.create_nat_gateway(
            SubnetId=subnet_id,
            AllocationId=eip["AllocationId"],
            TagSpecifications=[{"ResourceType": "natgateway", "Tags": _tags(name, tags)}],
        )
        nat_gateway_id = nat["NatGateway"]["NatGatewayId"]
        waiter = self.ec2.get_waiter("nat_gateway_available")
        waiter.wait(NatGatewayIds=[nat_gateway_id])
        return nat_gateway_id


class SecurityGroupManager:
    def __init__(self, ec2):
        self.ec2 = ec2

    @retryable
    def ensure_security_group(self, vpc_id: str, name: str, description: str, tags: Dict[str, str]) -> str:
        existing = self.ec2.describe_security_groups(Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "group-name", "Values": [name]},
        ]).get("SecurityGroups")
        if existing:
            return existing[0]["GroupId"]
        response = self.ec2.create_security_group(
            GroupName=name,
            Description=description,
            VpcId=vpc_id,
            TagSpecifications=[{"ResourceType": "security-group", "Tags": _tags(name, tags)}],
        )
        return response["GroupId"]

    def configure_rules(self, group_id: str, ingress_rules: List[Dict[str, Any]], egress_rules: List[Dict[str, Any]]) -> None:
        if ingress_rules:
            self._authorize_if_missing(group_id, ingress_rules, egress=False)
        if egress_rules:
            self._authorize_if_missing(group_id, egress_rules, egress=True)

    def _authorize_if_missing(self, group_id: str, rules: List[Dict[str, Any]], egress: bool) -> None:
        direction = "true" if egress else "false"
        existing = self.ec2.describe_security_group_rules(Filters=[
            {"Name": "group-id", "Values": [group_id]},
            {"Name": "is-egress", "Values": [direction]},
        ]).get("SecurityGroupRules", [])
        if existing:
            return
        permissions = _normalize_rules(rules)
        if egress:
            self.ec2.authorize_security_group_egress(GroupId=group_id, IpPermissions=permissions)
        else:
            self.ec2.authorize_security_group_ingress(GroupId=group_id, IpPermissions=permissions)


class IAMManager:
    def __init__(self, iam):
        self.iam = iam

    @retryable
    def ensure_instance_profile(self, name: str, role_name: str) -> str:
        try:
            self.iam.get_instance_profile(InstanceProfileName=name)
        except self.iam.exceptions.NoSuchEntityException:
            self.iam.create_instance_profile(InstanceProfileName=name)
            self.iam.add_role_to_instance_profile(InstanceProfileName=name, RoleName=role_name)
        return name


class LaunchTemplateManager:
    def __init__(self, ec2):
        self.ec2 = ec2

    @retryable
    def ensure_launch_template(self, name: str, image_id: str, instance_type: str, iam_profile: str, security_group_ids: List[str], user_data: Optional[str], tags: Dict[str, str]) -> str:
        try:
            response = self.ec2.describe_launch_templates(Filters=[{"Name": "launch-template-name", "Values": [name]}])
            if response["LaunchTemplates"]:
                return response["LaunchTemplates"][0]["LaunchTemplateId"]
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "InvalidLaunchTemplateName.NotFoundException":
                raise
        params = {
            "LaunchTemplateName": name,
            "LaunchTemplateData": {
                "ImageId": image_id,
                "InstanceType": instance_type,
                "IamInstanceProfile": {"Name": iam_profile},
                "SecurityGroupIds": security_group_ids,
                "TagSpecifications": [
                    {"ResourceType": "instance", "Tags": _tags(name, tags)},
                    {"ResourceType": "volume", "Tags": _tags(name, tags)},
                ],
            },
        }
        if user_data:
            params["LaunchTemplateData"]["UserData"] = user_data
        response = self.ec2.create_launch_template(**params)
        return response["LaunchTemplate"]["LaunchTemplateId"]


class AutoScalingManager:
    def __init__(self, autoscaling):
        self.autoscaling = autoscaling

    @retryable
    def ensure_auto_scaling_group(self, name: str, launch_template_id: str, subnets: List[str], desired: int, minimum: int, maximum: int, tags: Dict[str, str]) -> None:
        try:
            self.autoscaling.describe_auto_scaling_groups(AutoScalingGroupNames=[name])
            exists = True
        except self.autoscaling.exceptions.ResourceInUseFault:
            exists = False
        params = {
            "AutoScalingGroupName": name,
            "LaunchTemplate": {"LaunchTemplateId": launch_template_id},
            "VPCZoneIdentifier": ",".join(subnets),
            "DesiredCapacity": desired,
            "MinSize": minimum,
            "MaxSize": maximum,
            "Tags": [{"Key": k, "Value": v, "PropagateAtLaunch": True} for k, v in tags.items()],
        }
        if exists:
            self.autoscaling.update_auto_scaling_group(**params)
        else:
            self.autoscaling.create_auto_scaling_group(**params)

    @retryable
    def ensure_target_tracking_policy(self, name: str, asg_name: str, target: float) -> None:
        self.autoscaling.put_scaling_policy(
            AutoScalingGroupName=asg_name,
            PolicyName=name,
            PolicyType="TargetTrackingScaling",
            TargetTrackingConfiguration={
                "PredefinedMetricSpecification": {"PredefinedMetricType": "ASGAverageCPUUtilization"},
                "TargetValue": target,
            },
        )


class SecretsManager:
    def __init__(self, secrets_client):
        self.client = secrets_client

    @retryable
    def fetch_credentials(self, secret_name: str, username_key: str, password_key: str) -> Dict[str, str]:
        response = self.client.get_secret_value(SecretId=secret_name)
        secret_string = response.get("SecretString")
        if not secret_string:
            raise ValueError(f"Secret {secret_name} does not contain SecretString")
        secret = yaml.safe_load(secret_string)
        return {
            "username": secret[username_key],
            "password": secret[password_key],
        }


class RDSManager:
    def __init__(self, rds):
        self.rds = rds

    @retryable
    def ensure_postgres(self, config: Dict[str, Any], subnet_group: str, security_group_id: str, credentials: Dict[str, str], tags: Dict[str, str]) -> str:
        identifier = config["identifier"]
        try:
            self.rds.describe_db_instances(DBInstanceIdentifier=identifier)
            LOGGER.info("Reusing RDS instance %s", identifier)
            return identifier
        except self.rds.exceptions.DBInstanceNotFoundFault:
            pass
        params = {
            "DBInstanceIdentifier": identifier,
            "AllocatedStorage": config["allocated_storage"],
            "DBInstanceClass": config["instance_class"],
            "Engine": config["engine"],
            "EngineVersion": config["engine_version"],
            "MasterUsername": credentials["username"],
            "MasterUserPassword": credentials["password"],
            "DBSubnetGroupName": subnet_group,
            "VpcSecurityGroupIds": [security_group_id],
            "StorageEncrypted": config["storage_encrypted"],
            "BackupRetentionPeriod": config["backup_retention_days"],
            "PreferredBackupWindow": config["backup_window"],
            "PreferredMaintenanceWindow": config["maintenance_window"],
            "MultiAZ": config["multi_az"],
            "EnableIAMDatabaseAuthentication": config["iam_authentication"],
            "DeletionProtection": config["deletion_protection"],
            "DBName": config["db_name"],
            "Tags": [{"Key": k, "Value": v} for k, v in tags.items()],
        }
        if config["parameter_group_name"]:
            params["DBParameterGroupName"] = config["parameter_group_name"]
        self.rds.create_db_instance(**params)
        return identifier


class S3Manager:
    def __init__(self, s3):
        self.s3 = s3

    @retryable
    def ensure_bucket(self, bucket: str, region: str) -> None:
        try:
            self.s3.head_bucket(Bucket=bucket)
            LOGGER.info("Bucket %s already exists", bucket)
        except botocore.exceptions.ClientError as exc:
            error_code = exc.response["Error"]["Code"]
            if error_code in ("404", "NoSuchBucket"):
                LOGGER.info("Creating bucket %s", bucket)
                params = {"Bucket": bucket}
                if region != "us-east-1":
                    params["CreateBucketConfiguration"] = {"LocationConstraint": region}
                self.s3.create_bucket(**params)
            else:
                raise

    def enable_versioning(self, bucket: str) -> None:
        self.s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})

    def apply_encryption(self, bucket: str, encryption: Dict[str, Any]) -> None:
        config = {
            "Rules": [
                {
                    "ApplyServerSideEncryptionByDefault": {
                        "SSEAlgorithm": encryption["type"],
                    }
                }
            ]
        }
        if encryption.get("kms_key_arn"):
            config["Rules"][0]["ApplyServerSideEncryptionByDefault"]["KMSMasterKeyID"] = encryption["kms_key_arn"]
        self.s3.put_bucket_encryption(Bucket=bucket, ServerSideEncryptionConfiguration=config)


def _tags(name: str, extra: Dict[str, str]) -> List[Dict[str, str]]:
    tags = {"Name": name, **extra}
    return [{"Key": k, "Value": v} for k, v in tags.items()]


def main(config_path: str = "config/base.yaml") -> None:
    config = load_config(config_path)
    project = config["project"]
    region = project["region"]
    env = project["environment"]

    clients = init_clients(region)
    tags = {"Project": project["name"], "Environment": env}

    vpc_manager = VPCManager(clients.ec2)
    vpc_id = vpc_manager.ensure_vpc(f"{project['name']}-{env}", config["network"]["vpc"]["cidr"], tags)
    igw_id = vpc_manager.ensure_internet_gateway(vpc_id, f"{project['name']}-{env}-igw", tags)

    azs = clients.ec2.describe_availability_zones(Filters=[{"Name": "region-name", "Values": [region]}])["AvailabilityZones"]
    az_names = [az["ZoneName"] for az in azs][: config["network"]["vpc"]["az_count"]]
    public_subnets = []
    for cidr, az in zip(config["network"]["subnets"]["public"]["cidr_blocks"], az_names):
        subnet_id = vpc_manager.ensure_subnet(f"public-{az}", vpc_id, cidr, az, True, {**tags, "Tier": "public"})
        public_subnets.append(subnet_id)
    private_subnets = []
    for cidr, az in zip(config["network"]["subnets"]["private"]["cidr_blocks"], az_names):
        subnet_id = vpc_manager.ensure_subnet(f"private-{az}", vpc_id, cidr, az, False, {**tags, "Tier": "private"})
        private_subnets.append(subnet_id)

    s3_manager = S3Manager(clients.s3)
    s3_manager.ensure_bucket(config["s3"]["raw_bucket"], region)
    s3_manager.enable_versioning(config["s3"]["raw_bucket"])
    s3_manager.ensure_bucket(config["s3"]["curated_bucket"], region)
    s3_manager.enable_versioning(config["s3"]["curated_bucket"])

    table = Table(title="Provisioned Artefacts")
    table.add_column("Resource")
    table.add_column("Identifiers")
    table.add_row("VPC", vpc_id)
    table.add_row("InternetGateway", igw_id)
    table.add_row("PublicSubnets", ", ".join(public_subnets))
    table.add_row("PrivateSubnets", ", ".join(private_subnets))
    console.print(table)


if __name__ == "__main__":
    config_file = os.getenv("INFRA_CONFIG", "config/base.yaml")
    main(config_file)

