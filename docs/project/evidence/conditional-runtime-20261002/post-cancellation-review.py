"""Queue one owner notice through company records; only signed Slack cancels the prior program."""

import json
import sys
from datetime import UTC, datetime

from quant_company.company import Company, fingerprint, stable
from quant_company.config import Settings
from quant_company.research.builds import profile_for
from quant_company.research.program_contracts import ResearchProgram
from quant_company.research.programs import ProgramStore
from quant_company.research.scientific_lineages import history

company = Company(Settings())
assert company.settings.company_code_commit == '231d6ba0755f658f167636a8ea2c3ef65b6af562'
spec = ResearchProgram.model_validate_json(sys.argv[1])
digest = fingerprint(spec.model_dump(mode='json'))
assert digest == 'be0d940bf5904c417d213898617b3f6c0fed19d2bfddd78460ec315e92b216af'
project_id = '9aac0de4-2b97-5195-a720-287d324234f3'
prior_id = 'e06537d3-fac3-5c8c-bf25-ddabb3c7e282'
prior_digest = '53392822414ca32e89fab0f3a9a1093a350196345bd13084654a45510297159b'
task_id = stable('operator-conditional-review:' + project_id + ':' + digest)
message_id = stable('operator-prior-cancel-notice:' + project_id + ':' + digest)
text = (
    '새 조건부 연구 프로그램의 운영 준비를 마쳤습니다.\n'
    '실제 RTX 3070의 104거래일 입력 검증, 운영 소스·입력·프로필·원문 근거와 이전 10개 과제 이력 대조를 확인했습니다. '
    '새 연구 실험과 성과 산출은 아직 시작하지 않았습니다.\n\n'
    '새 제안: 현재 고정 빈티지 자료와 명시한 공개시각 가정에 조건부인 ETF 개발 연구입니다. '
    '과거 공개시각·수정 빈티지·최초 준비 바이트의 공백을 명시합니다. '
    'PIT 인증, 실제 체결 성과, 확증 또는 운영 배치의 근거로 사용할 수 없습니다. '
    '누적 상한은 4개 과학 시행·7,200초·1미션·동시 1개이며, 첫 회차는 최대 2결과입니다. '
    '기존 10개 과제의 차단·중복 이력을 상속하고 직원들이 데이터 적합성과 과제 선택을 독립 판단합니다.\n'
    '새 프로그램 전체 해시: ' + digest + '\n\n'
    '기존 프로그램의 서명된 취소가 먼저 필요합니다. 이 연구센터 스레드에서 아래 문장을 그대로 보내 주세요.\n'
    '`연구 프로그램 취소 ' + prior_id + ' ' + prior_digest + '`\n'
    '취소가 기록되면 새 프로그램의 전체 사양과 별도 승인 요청을 게시합니다. '
    '이 안내 자체는 새 프로그램 승인이나 직원 심사 결과가 아닙니다.'
)
with company.db.transaction() as connection:
    project = company._project(connection, project_id)
    assert project['revision'] == 5 and project['status'] == 'active'
    assert project['owner_user'] == 'U0C250E23NW' and project['channel'] == 'C0C2B9EUEGM'
    assert project['thread_ts'] == '1789633942.673909'
    assert project['owner_user'] in company.settings.slack_allowed_users
    assert project['channel'] in company.settings.slack_allowed_channels
    for envelope in spec.envelopes:
        profile_for(company, envelope.template)
        preimage, preimage_digest = history(connection, project['id'], envelope.template.scientific_lineage)
        assert preimage_digest == envelope.template.scientific_lineage.history_digest
    prior = ProgramStore(company).snapshot(connection, prior_id)
    assert prior['manifest_digest'] == prior_digest
    if prior['state'] == 'cancelled':
        print(json.dumps({'state': 'prior_already_cancelled', 'notice_queued': False}))
        raise SystemExit(0)
    assert prior['state'] == 'active'
    assert not connection.execute("SELECT 1 FROM research_programs WHERE project_id=%s AND state='draft'",
        (project_id,)).fetchone()
    connection.execute("""INSERT INTO tasks(id,project_id,agent,instruction,revision,kind,status)
        VALUES(%s,%s,'director',%s,5,'operator_program_review','completed') ON CONFLICT DO NOTHING""",
        (task_id, project_id, 'INTENT-v8 operator notice: signed prior cancellation and fresh conditional program review'))
    old = connection.execute('SELECT text FROM messages WHERE id=%s', (message_id,)).fetchone()
    if old:
        assert old['text'] == text
    else:
        company._message(connection, project, task_id, 'director', 'status', text,
            message_id=str(message_id), notify_owner=True)
        company._event(connection, 'operator_conditional_intake_review_requested', {
            'authorization': 'INTENT-v8 conversation operational intake', 'program_digest': digest,
            'prior_program_id': prior_id, 'signed_prior_cancellation_required': True,
            'new_program_approval_granted': False}, project_id)
    outbox = connection.execute('SELECT status,sent_ts FROM outbox WHERE id=%s', (message_id,)).fetchone()
    assert outbox
print(json.dumps({'schema_version': 1, 'observed_at': datetime.now(UTC).isoformat(),
    'project_id': project_id, 'message_id': str(message_id), 'program_digest': digest,
    'outbox_status': outbox['status'], 'sent_ts': outbox['sent_ts'],
    'state': 'awaiting_signed_prior_cancellation', 'new_program_authorized': False,
    'scientific_trials_started': 0}))
