"""Read selected frozen public-paper excerpts from a private qualification receipt."""

import json
import re
import sys
from pathlib import Path

receipt = json.loads(Path(sys.argv[1]).read_text())
wanted = set(sys.argv[2:])
for page_index, page in enumerate(receipt["inputs"]["positive"]["pages"], 1):
    text, start, pieces = page["text"], 0, []
    while start < len(text):
        end = min(len(text), start + 500)
        if end < len(text):
            segment = text[start + 250:end]
            boundaries = list(re.finditer(r"[.!?]\s+", segment)) or list(re.finditer(r"(?<!-)\n", segment))
            if boundaries:
                end = start + 250 + boundaries[-1].end()
        pieces.append(text[start:end])
        start = end
    if len(pieces) > 1 and len(pieces[-1]) < 8:
        pieces[-2:] = [pieces[-2] + pieces[-1]]
    for span_index, excerpt in enumerate(pieces, 1):
        identity = f"p{page_index}-s{span_index}"
        if identity in wanted:
            print(json.dumps({"span_id": identity, "location": page["location"], "text": excerpt},
                             ensure_ascii=False))
