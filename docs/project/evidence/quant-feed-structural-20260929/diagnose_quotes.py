"""Read-only exact-quote diagnostics; output stays private, not a publication."""

import json
import re
import sys
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path


def normalized(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


receipt = json.loads(Path(sys.argv[1]).read_text())
pages = receipt["inputs"]["positive"]["pages"]
rows = []
for call in receipt["calls"]:
    for issue in call.get("validation_issues", []):
        if issue["code"] != "quant_quote_not_in_original_version":
            continue
        index = int(re.search(r"\d+", issue["field"])[0])
        evidence = json.loads(call["response"]["decision"]["artifacts"][0]["content"])["evidence"][index]
        quote = normalized(evidence["quote"])
        candidates = []
        for page in pages:
            text = normalized(page["text"])
            match = SequenceMatcher(None, quote, text, autojunk=False).find_longest_match()
            candidates.append((match.size, page["location"], text[max(0, match.b-120):match.b+len(quote)+120]))
        size, location, nearby = max(candidates)
        rows.append({"case": call["case"], "field": issue["field"], "quote": quote,
                     "claimed_location": evidence["location"], "closest_location": location,
                     "longest_exact_fraction": round(size / max(1, len(quote)), 3), "source_context": nearby})
print(json.dumps(rows, ensure_ascii=False, indent=2))
