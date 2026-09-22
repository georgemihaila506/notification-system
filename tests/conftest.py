"""Shared fixtures: moto mocks DynamoDB in-process — no real AWS, no cost."""

from __future__ import annotations

import os

import boto3
import pytest
from moto import mock_aws

from notifier.db import Store

os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "eu-north-1")
# handlers.worker builds SENDERS at import, and the SES sender needs its From
# identity then — deliberately fail-fast, so a misconfigured Lambda dies on the
# first cold start rather than once per message. Set it before any test imports.
os.environ.setdefault("SES_SOURCE", "notify@example.com")
os.environ.setdefault("SES_CONFIG_SET", "notify-events")

PREFS_TABLE = "prefs-test"
DELIVERIES_TABLE = "deliveries-test"


def _create(ddb, name: str) -> None:
    ddb.create_table(
        TableName=name,
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.fixture
def store():
    """A Store over fresh moto prefs + deliveries tables (per test)."""
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="eu-north-1")
        _create(ddb, PREFS_TABLE)
        _create(ddb, DELIVERIES_TABLE)
        yield Store(PREFS_TABLE, DELIVERIES_TABLE, dynamodb=ddb)


@pytest.fixture
def api_env(monkeypatch):
    """Tables + an SNS topic with an SQS queue subscribed to it (so tests can SEE what
    was published), plus the env vars the ingest handler reads. Yields (store, queue)."""
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="eu-north-1")
        _create(ddb, PREFS_TABLE)
        _create(ddb, DELIVERIES_TABLE)
        topic_arn = boto3.client("sns", region_name="eu-north-1").create_topic(Name="t")["TopicArn"]
        sqs = boto3.resource("sqs", region_name="eu-north-1")
        queue = sqs.create_queue(QueueName="observe")
        boto3.client("sns", region_name="eu-north-1").subscribe(
            TopicArn=topic_arn, Protocol="sqs", Endpoint=queue.attributes["QueueArn"]
        )
        monkeypatch.setenv("PREFS_TABLE", PREFS_TABLE)
        monkeypatch.setenv("DELIVERIES_TABLE", DELIVERIES_TABLE)
        monkeypatch.setenv("TOPIC_ARN", topic_arn)
        yield Store(PREFS_TABLE, DELIVERIES_TABLE, dynamodb=ddb), queue
