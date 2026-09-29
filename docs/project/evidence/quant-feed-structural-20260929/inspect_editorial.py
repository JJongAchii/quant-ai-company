"""Read-only, bounded private critique view; omit originals, prompts and prose drafts."""

import json
import sys
from pathlib import Path


receipt = json.loads(Path(sys.argv[1]).read_text())
views = []
for call in receipt.get("calls", []):
    view = {key: call.get(key) for key in ("case", "stage", "state", "disposition", "validation_issues")}
    if call.get("stage") == "critique" and call.get("response"):
        content = call["response"]["decision"]["artifacts"][0]["content"]
        critique = json.loads(content)
        view.update(reason=critique.get("reason"), issues=critique.get("issues"),
                    notes=critique.get("notes"))
    if call.get("stage") in {"review", "repair", "revision"} and call.get("response") and len(sys.argv) > 2:
        content = call["response"]["decision"]["artifacts"][0]["content"]
        draft = json.loads(content)
        view["selected_statements"] = {field: draft.get(field) for field in
                                       ("market", "idea", "data_period", "validation", "author_results", "limitations")}
    views.append(view)
print(json.dumps({"state": receipt.get("state"), "case_results": receipt.get("case_results"),
                  "calls": views, "card_characters": len(receipt.get("card", ""))}, ensure_ascii=False, indent=2))
