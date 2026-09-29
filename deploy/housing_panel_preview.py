#!/usr/bin/env python3
"""Queue one durable, clearly labelled Slack map panel preview using an official notice."""

import json
import os
from datetime import timedelta

from quant_company.company import Company, stable
from quant_company.config import Settings
from quant_company.housing_feed import schedule
from quant_company.housing_feed.contracts import HousingNotice
from quant_company.housing_feed.render import render
from quant_company.housing_feed.store import HousingFeedStore


def queue_preview():
    company = Company(Settings())
    store = HousingFeedStore(company)
    if (not store.authorized() or not company.settings.housing_feed_publish_enabled
            or not company.settings.housing_map_panel_enabled):
        raise ValueError('housing_panel_preview_not_authorized')
    commit = os.environ.get('COMPANY_CODE_COMMIT', '')
    if len(commit) != 40:
        raise ValueError('housing_panel_preview_release_unknown')
    at = schedule.utcnow()
    channel = company.settings.housing_feed_channel_id
    owner = company.settings.housing_feed_owner_user
    with company.db.transaction() as conn:
        rows = conn.execute("""SELECT n.id,n.payload,n.digest FROM housing_feed_notices n
            JOIN housing_feed_sources s ON s.id=n.source_id
            WHERE n.active AND s.error IS NULL AND n.checked_at > %s
            ORDER BY n.checked_at DESC,n.id LIMIT 30""", (at - timedelta(hours=3),)).fetchall()
        selected = next(((row, HousingNotice.model_validate(row['payload'])) for row in rows
                         if row['payload'].get('map_location') and row['payload'].get('address')), None)
        if not selected:
            raise ValueError('housing_panel_preview_no_recent_mapped_notice')
        row, notice = selected
        event_key = f"{notice.id}:{row['digest']}:panel_preview:{commit}"
        existing = conn.execute("""SELECT id FROM housing_feed_publications
            WHERE channel=%s AND event_key=%s""", (channel, event_key)).fetchone()
        if existing:
            return {'state': 'already_queued', 'message_id': str(existing['id']), 'notice_id': notice.id}
        project_id = stable(f"housing-feed-project:{channel}:{owner}")
        conn.execute("""INSERT INTO projects(id,title,instruction,owner_user,channel)
            VALUES(%s,'housing-feed','서울·경기 공식 주택분양 공고와 접수 일정 알림',%s,%s)
            ON CONFLICT DO NOTHING""", (project_id, owner, channel))
        project = conn.execute('SELECT * FROM projects WHERE id=%s', (project_id,)).fetchone()
        identity = stable(f"housing-feed:{channel}:{event_key}")
        content = ('*지도 패널 표시 시험*\n기존 공고의 지도 패널을 확인하는 메시지입니다. '
                   '새 청약 공고가 아닙니다.\n\n' + render(notice, at))
        company._message(conn, project, None, 'reporter', 'housing_feed', content, message_id=identity)
        conn.execute("""INSERT INTO housing_feed_publications
            (id,notice_id,notice_digest,channel,event_key,policy_digest,expires_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                     (identity, notice.id, row['digest'], channel, event_key,
                      store.policy(), at + timedelta(days=1)))
        conn.execute('UPDATE outbox SET next_at=%s WHERE id=%s', (schedule.delivery_time(at), identity))
    return {'state': 'queued', 'message_id': str(identity), 'notice_id': notice.id}


if __name__ == '__main__':
    print(json.dumps(queue_preview()))
