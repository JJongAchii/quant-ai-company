import json

from ..company import PolicyError
from .contracts import VideoAction
from .store import VideoStore

LABELS = {"approve": "공개", "revise": "수정", "hold": "보류"}


def blocks(company, row):
    with company.db.transaction() as conn:
        job = conn.execute("SELECT * FROM video_jobs WHERE review_message_id=%s", (row["id"],)).fetchone()
    if not job or job["state"] != "awaiting_approval":
        return None
    value = json.dumps({"id": str(job["id"]), "version": job["version"], "digest": job["artifact_digest"]}, separators=(",", ":"))
    return [{"type": "section", "text": {"type": "mrkdwn", "text": row["text"]}},
            {"type": "actions", "block_id": "video-review:" + str(job["id"]), "elements": [
                {"type": "button", "action_id": "video_"+action, "text": {"type": "plain_text", "text": label},
                 "value": value} for action, label in LABELS.items()]}]


def accept(ingress, role, payload, credential):
    try:
        if (role != "market_brief" or payload["type"] != "block_actions"
                or payload["team"]["id"] != ingress.settings.slack_team_id
                or payload["api_app_id"] != credential["app_id"] or len(payload["actions"]) != 1):
            raise ValueError("Unexpected video interaction")
        action = payload["actions"][0]
        selected = action["action_id"].removeprefix("video_")
        message, container = payload["message"], payload["container"]
        binding = json.loads(action["value"])
        if (action['action_id'] != 'video_'+selected or selected not in LABELS or action["type"] != "button" or action["text"]["text"] != LABELS[selected]
                or action["block_id"] != "video-review:"+binding["id"]
                or message["user"] != credential["bot_user_id"]
                or message.get("app_id", credential["app_id"]) != credential["app_id"]
                or message["ts"] != container["message_ts"] or payload["channel"]["id"] != container["channel_id"]
                or container["type"] != "message" or container.get("is_ephemeral")):
            raise ValueError("Invalid video message binding")
        value = VideoAction(action=selected, version=binding["version"], artifact_digest=binding["digest"])
        result = VideoStore(ingress.company).action(binding["id"], value, payload["user"]["id"],
            payload["channel"]["id"], f"video:{binding['id']}:{action['action_ts']}:{payload['user']['id']}",
            message_ts=message["ts"])
        return {"ok": True, "video_action": True, **result}
    except (KeyError, TypeError, ValueError, AttributeError):
        raise PolicyError("Malformed video Slack interaction") from None
