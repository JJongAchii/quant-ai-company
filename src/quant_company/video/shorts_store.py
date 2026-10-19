"""Records of the daily Shorts (video/shorts.py): one cut per delivered episode, in the episode's Slack thread.

The episode always goes first: a cut starts only when no episode is in production and is given up once the next
episode has started (design §7). Its paid steps use the episode's effect receipts (video_effects, job_id = episode)."""

from datetime import UTC, datetime, time, timedelta

from psycopg.types.json import Jsonb

from ..company import PolicyError, stable
from . import store as video_store
from .store import ACTIVE as EPISODE_ACTIVE
from .store import EDITION_NAMES, KST, LOCK, VideoStore

ACTIVE = ('writing', 'synthesizing', 'rendering')
# The next episode is usually in production then (briefs at about 07:45 and 16:10-17:25, then about 1.5 hours of
# script, speech and render): a cut takes no step that would hold the video worker or the Claude lane.
QUIET = ((time(6, 30), time(9, 30)), (time(15, 30), time(19, 0)))


def quiet(at):
    now = at.astimezone(KST).time()
    return any(start <= now < end for start, end in QUIET)
# Owner file names in the thread: the episode's stem with a Shorts suffix.
FILES = (('shorts.mp4', '_쇼츠.mp4', '쇼츠 영상'), ('shorts.srt', '_쇼츠.srt', '쇼츠 자막'),
         ('shorts-upload.txt', '_쇼츠_업로드문안.txt', '쇼츠 업로드 문안'))


def names(job):
    day = str(job['source']['day']).replace('-', '')
    stem = ('마감브리핑_' if job['source'].get('edition_kind') == 'close' else '아침브리핑_') + day
    return [(source, stem + suffix, title) for source, suffix, title in FILES]


def delivery_text(job, artifacts):
    findings = artifacts.get('findings') or []
    check = '자동 검사 통과' if not findings else f'자동 검사 지적 {len(findings)}건(아래, 제작은 계속함)'
    notes = ''.join(f'• {f[:180]}\n' for f in findings[:5])
    return (f"쇼츠 준비 완료 · {EDITION_NAMES.get(job['source'].get('edition_kind'), '브리핑')}\n{artifacts.get('title', '')}\n"
            f"길이 {round(artifacts.get('duration', 0))}초 · 세로 1080×1920 · {check}\n" + notes +
            "첨부: 쇼츠 영상(MP4) · 자막(SRT) · 업로드 문안\n"
            "업로드 후 YouTube Studio에서 '관련 동영상'을 오늘 본편으로 지정해 주세요.")


class ShortsStore:
    def __init__(self, company):
        self.company, self.settings, self.db = company, company.settings, company.db
        self.video = VideoStore(company)

    def enabled(self):
        return self.settings.video_shorts_enabled and self.video.enabled()

    def claim(self):
        """The next due step of a cut, starting the cut of the latest delivered episode when there is none."""
        if not self.enabled():
            return None
        with self.db.transaction() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK,))
            given_up = conn.execute("""UPDATE video_shorts s SET state='skipped',error='superseded',lease_until=NULL,
                updated_at=now() FROM video_jobs j WHERE j.id=s.job_id AND s.state=ANY(%s)
                AND EXISTS (SELECT 1 FROM video_jobs n WHERE n.created_at>j.created_at) RETURNING s.job_id""",
                                    (list(ACTIVE),)).fetchall()
            # An episode in production goes first; one stuck for a day (a lost brief post) does not stop the cuts.
            if quiet(video_store.utcnow()) or conn.execute("""SELECT 1 FROM video_jobs WHERE state=ANY(%s)
                    AND created_at>now()-interval '24 hours' LIMIT 1""", (list(EPISODE_ACTIVE),)).fetchone():
                row = None
            else:
                # Only the newest episode, delivered in the last 12 hours: nothing older is cut after a release.
                conn.execute("""INSERT INTO video_shorts(job_id) SELECT j.id FROM video_jobs j
                    WHERE j.state='delivered' AND j.updated_at>now()-interval '12 hours'
                    AND j.policy->>'template'='motion-v2' AND j.policy->>'delivery'='slack'
                    AND NOT EXISTS (SELECT 1 FROM video_jobs n WHERE n.created_at>j.created_at)
                    ON CONFLICT DO NOTHING""")
                row = conn.execute("""SELECT * FROM video_shorts WHERE state=ANY(%s)
                    AND (lease_until IS NULL OR lease_until<now()) ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED""",
                                   (list(ACTIVE),)).fetchone()
                if row:
                    conn.execute("UPDATE video_shorts SET lease_until=now()+interval '60 minutes' WHERE job_id=%s",
                                 (row['job_id'],))
        for gone in given_up:
            self.note(gone['job_id'], '쇼츠는 다음 회차 제작이 시작돼 만들지 않았습니다.')
        return row

    def superseded(self, job_id):
        with self.db.transaction() as conn:
            return bool(conn.execute("""SELECT 1 FROM video_jobs j JOIN video_jobs n ON n.created_at>j.created_at
                WHERE j.id=%s LIMIT 1""", (job_id,)).fetchone())

    def get(self, job_id):
        with self.db.transaction() as conn:
            return conn.execute('SELECT * FROM video_shorts WHERE job_id=%s', (job_id,)).fetchone()

    def save(self, row, state, **values):
        if not values.keys() <= {'plan', 'qa', 'artifacts', 'error'}:
            raise ValueError('Unknown Shorts update')
        assignments, parameters = ['state=%s', 'lease_until=NULL', 'updated_at=now()'], [state]
        for key, value in values.items():
            assignments.append(key + '=%s')
            parameters.append(value if key == 'error' else Jsonb(value))
        with self.db.transaction() as conn:
            if not conn.execute('UPDATE video_shorts SET ' + ','.join(assignments) + ' WHERE job_id=%s AND state=%s',
                                (*parameters, row['job_id'], row['state'])).rowcount:
                raise PolicyError('Shorts changed while processing')

    def wait(self, row, seconds, reason):
        with self.db.transaction() as conn:
            conn.execute('UPDATE video_shorts SET error=%s,lease_until=%s,updated_at=now() WHERE job_id=%s AND state=%s',
                         (reason, datetime.now(UTC) + timedelta(seconds=seconds), row['job_id'], row['state']))

    def skip(self, row, reason, text):
        """Given up for this episode, with one line in its thread; the episode itself is never affected."""
        self.save(row, 'skipped', error=reason)
        self.note(row['job_id'], text)

    def note(self, job_id, text, notify_owner=False):
        with self.db.transaction() as conn:
            job = conn.execute('SELECT * FROM video_jobs WHERE id=%s', (job_id,)).fetchone()
            project = self.company._project(conn, job['source']['project_id'])
            if project['thread_ts'] and project['status'] == 'active':
                self.company._message(conn, project, None, 'market_brief', 'video_status', text,
                                      message_id=stable(f'video-shorts-note:{job_id}:{text}'), notify_owner=notify_owner)

    # ---- Delivery (Slack dispatcher, video/slack_files.py) -------------------------------------------------------

    def deliver(self, row, job, artifacts):
        with self.db.transaction() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK,))
            current = conn.execute('SELECT * FROM video_shorts WHERE job_id=%s FOR UPDATE', (row['job_id'],)).fetchone()
            if current['state'] != 'rendering':
                raise PolicyError('Shorts changed while processing')
            project = self.company._project(conn, job['source']['project_id'])
            if not project['thread_ts'] or project['status'] != 'active':
                raise PolicyError('Brief thread receipt missing')
            mid = stable(f"video-shorts:{job['id']}")
            # Ready files wait for the owner's upload: the message mentions the owner (2026-10-19 tag rule).
            self.company._message(conn, project, None, 'market_brief', 'video_shorts', delivery_text(job, artifacts),
                                  message_id=mid, notify_owner=True)
            conn.execute("""UPDATE video_shorts SET state='delivering',artifacts=%s,delivery_message_id=%s,error=NULL,
                lease_until=NULL,updated_at=now() WHERE job_id=%s""", (Jsonb(artifacts), mid, row['job_id']))
            return str(mid)

    def delivery_gate(self, conn, row):
        current = conn.execute('SELECT s.state AS shorts_state,j.* FROM video_shorts s JOIN video_jobs j ON j.id=s.job_id '
                               'WHERE s.delivery_message_id=%s', (row['id'],)).fetchone()
        if current and current['shorts_state'] == 'delivering' and row['thread_ts'] and self.video.allowed(current):
            return True
        conn.execute("UPDATE outbox SET status='stale',error='video_shorts_changed' WHERE id=%s", (row['id'],))
        return False

    def delivery_job(self, message_id):
        """What the dispatcher uploads: the episode's source with the cut's artifacts."""
        with self.db.transaction() as conn:
            row = conn.execute('SELECT s.artifacts AS shorts,j.* FROM video_shorts s JOIN video_jobs j ON j.id=s.job_id '
                               'WHERE s.delivery_message_id=%s', (message_id,)).fetchone()
        return {**row, 'artifacts': row['shorts']} if row else None

    def delivery_receipt(self, message_id, receipt):
        with self.db.transaction() as conn:
            conn.execute('UPDATE video_shorts SET qa=qa||%s,updated_at=now() WHERE delivery_message_id=%s',
                         (Jsonb({'delivery': receipt}), message_id))

    def delivery_result(self, message_id, state, receipt, error=None):
        """delivered | failed (nothing shared) | uncertain (completion outcome unknown, never replayed)."""
        final = {'delivered': 'delivered', 'failed': 'delivery_failed', 'uncertain': 'delivery_uncertain'}[state]
        with self.db.transaction() as conn:
            row = conn.execute("""UPDATE video_shorts SET state=%s,error=%s,qa=qa||%s,updated_at=now()
                WHERE delivery_message_id=%s AND state='delivering' RETURNING job_id,artifacts""",
                               (final, error, Jsonb({'delivery': receipt}), message_id)).fetchone()
        if row and state != 'delivered':
            # The owner fetches the files by hand: a failed delivery needs the owner (2026-10-19 tag rule).
            self.note(row['job_id'], '쇼츠 파일 전달 ' + ('실패: 파일이 공유되지 않았습니다.' if state == 'failed' else
                                                     '결과 확인 필요: 공유됐는지 알 수 없어 같은 파일을 자동으로 다시 올리지 않습니다.')
                      + f" 사유: {error}\n서버 보관 경로: {row['artifacts']['directory']}", notify_owner=True)

    def sync_deliveries(self):
        with self.db.transaction() as conn:
            rows = conn.execute("""SELECT s.delivery_message_id AS id,s.qa->'delivery' AS receipt,o.status,o.error FROM video_shorts s
                JOIN outbox o ON o.id=s.delivery_message_id WHERE s.state='delivering'
                AND o.status IN ('uncertain','blocked','stale')""").fetchall()
        for row in rows:
            # The stored file IDs stay with the result: they are what the owner checks by hand.
            self.delivery_result(row['id'], 'uncertain' if row['status'] == 'uncertain' else 'failed', row['receipt'] or {},
                                 row['error'] or 'outbox_' + row['status'])
        return len(rows)
