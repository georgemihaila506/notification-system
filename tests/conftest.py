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
