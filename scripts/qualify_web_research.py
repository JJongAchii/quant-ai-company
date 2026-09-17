"""Opt-in live subscription/web qualification in a disposable local database; never dispatches Slack."""

import argparse
import asyncio
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from quant_company.company import Company, as_json
from quant_company.config import Settings
from quant_company.execution import TurnExecutor
from quant_company.providers.codex_runner import CodexRunner, RunnerConfig


async def qualify(output):
    admin = os.environ.get('TEST_DATABASE_URL')
    if not admin:
        admin = json.loads(Path('.local/test-env.json').read_text())['database_url']
    database = 'company_web_qualification_' + uuid4().hex
    connection = conninfo_to_dict(admin)
    if connection.get('host') not in {'127.0.0.1', 'localhost', '::1'}:
        raise ValueError('Qualification requires a disposable local PostgreSQL cluster')
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(database)))
    connection['dbname'] = database
    report = {'started_at': datetime.now(UTC).isoformat(), 'state': 'failed', 'live_codex': True,
              'live_web': True, 'slack_dispatched': False, 'turns': []}
    report['code_commit'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    report['uncommitted_code'] = bool(subprocess.check_output(
        ['git', 'diff', 'HEAD', '--', 'src', 'scripts/qualify_web_research.py'], text=True))
    try:
        company = Company(Settings(database_url=make_conninfo(**connection), company_max_task_turns=8,
                                   company_max_daily_turns=16))
        company.db.migrate()
        query = ('현재 연준 금리 결정을 최신 공식 자료를 실제 웹 검색해서 분석해줘. 이번 검증은 다른 직원에게 '
                 '위임하지 말고 직접 처리해. 가장 최근 FOMC 성명과 시행 지침 원문을 확인하고, 검색 후보는 '
                 '원문을 읽기 전 인용하지 마. 확인한 사실과 해석을 구분하고 원문 링크, 발표일, 수집 시각과 '
                 '확인하지 못한 내용을 적어줘. 최종 결과는 출처 ID를 포함한 artifact로 남겨줘.')
        company.ingest(event_key='live-web-qualification', text=query, owner='qualification')
        runner = CodexRunner(RunnerConfig(
            codex_home=Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))),
            jobs_dir=output.parent / ('codex-jobs-' + database), timeout_seconds=300))
        executor = TurnExecutor(company, runner)
        for _ in range(8):
            with company.db.transaction() as conn:
                queued = conn.execute("SELECT id FROM turns WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if not queued:
                break
            result = await executor.execute(str(queued['id']))
            report['turns'].append({'id': str(queued['id']), **result})
            print(json.dumps(report['turns'][-1]), flush=True)
            if result['state'] != 'completed':
                break
        with company.db.transaction() as conn:
            calls = conn.execute('SELECT operation,arguments,receipt FROM web_requests ORDER BY created_at').fetchall()
            sources = conn.execute("SELECT id,title,uri,metadata FROM sources WHERE id LIKE 'web:%%'").fetchall()
            artifacts = conn.execute('SELECT title,content,source_ids FROM artifacts').fetchall()
            tasks = conn.execute('SELECT agent,status,error FROM tasks').fetchall()
            answers = conn.execute("SELECT text FROM messages WHERE kind='answer' ORDER BY created_at").fetchall()
        report.update(web_requests=calls, sources=sources, artifacts=artifacts, tasks=tasks,
                      answers=answers,
                      completed_at=datetime.now(UTC).isoformat())
        native = any(row['operation'] == 'web_search' and (row['receipt'] or {}).get('search_events') for row in calls)
        source_ids = {s['id'] for s in sources}
        cited = any(set(a['source_ids']) & source_ids for a in artifacts)
        report['state'] = 'passed' if native and source_ids and cited and all(t['status'] == 'completed' for t in tasks) else 'failed'
    finally:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(as_json(report), ensure_ascii=False, indent=2))
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database)))
    return report['state']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--output', type=Path, default=Path('.local/web-qualification.json'))
    args = parser.parse_args()
    result = asyncio.run(qualify(args.output.resolve()))
    print(result)
    raise SystemExit(0 if result == 'passed' else 1)
