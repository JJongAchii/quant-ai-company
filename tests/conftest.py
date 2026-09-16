import json
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from quant_company.company import Company
from quant_company.config import Settings
from quant_company.contracts import Role


@pytest.fixture
def database_url():
    url = os.environ.get("TEST_DATABASE_URL")
    local = Path(".local/test-env.json")
    if not url and local.exists():
        url = json.loads(local.read_text())["database_url"]
    if not url:
        pytest.skip("Real PostgreSQL required: set TEST_DATABASE_URL to a disposable test cluster")
    name = "company_test_" + uuid4().hex
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
    config = conninfo_to_dict(url)
    config["dbname"] = name
    yield make_conninfo(**config)
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def test_roles():
    names = ["director", "financial_strategist", "researcher_kr", "data"]
    return {name: Role(id=name, name=name, mission="Integration fixture", model="gpt-5.6-luna",
                       instructions="Synthetic fixture only", tools=["calculate", "knowledge_search", "read_source"],
                       can_delegate_to=[peer for peer in names if peer != name], active=True) for name in names}


@pytest.fixture
def company(database_url, test_roles):
    settings = Settings(database_url=database_url, operator_token="test-token-with-more-than-24-characters",
                        model_provider="fixture", fixture_mode=True,
                        slack_team_id="TTEST", slack_allowed_users=["UHUMAN"], slack_allowed_channels=["CQUANT"])
    company = Company(settings, test_roles)
    company.db.migrate()
    return company


@pytest.fixture
def credentials():
    return {role: {"app_id": f"A{index}", "bot_user_id": f"UBOT{index}",
                   "bot_token": f"fake-bot-token-{index}", "signing_secret": f"test-signing-secret-{index}"}
            for index, role in enumerate(["director", "financial_strategist", "researcher_kr", "data"])}


def queued_turns(company, project_id):
    with company.db.transaction() as conn:
        return [str(row["id"]) for row in conn.execute("""SELECT t.id FROM turns t JOIN tasks k ON k.id=t.task_id
            WHERE k.project_id=%s AND t.status='queued' ORDER BY t.created_at""", (project_id,)).fetchall()]
