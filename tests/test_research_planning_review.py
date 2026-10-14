"""Planning critique and decision on real PostgreSQL; fixture employees, no model calls."""


def test_decision_table_migrates_twice(company):
    company.db.migrate()
    with company.db.transaction() as conn:
        assert conn.execute("SELECT to_regclass('research_planning_decisions') AS name").fetchone()["name"]
