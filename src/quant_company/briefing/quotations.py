"""Input-bound references to complete, unchanged original text."""

import hashlib
import re

QUOTE_REFERENCE_VERSION = 1
PREFIX = "@original:"


def source_spans(document):
    content = document["content"]
    digest = hashlib.sha256(content.encode()).hexdigest()
    spans, start = [], 0
    while start < len(content):
        end = min(start + 340, len(content))
        if end < len(content):
            boundaries = list(re.finditer(r"\n|(?<=[.!?。])\s+", content[start:end]))
            if boundaries and boundaries[-1].end() >= 160:
                end = start + boundaries[-1].end()
            if len(content) - end < 10:
                end = len(content)
        text = content[start:end]
        identity = f"{document['id']}:{digest}:{start}:{end}"
        reference = PREFIX + hashlib.sha256(identity.encode()).hexdigest()[:20]
        spans.append((reference, text))
        start = end
    return spans


def quotation_index(bundle):
    index = {}
    for document in bundle["documents"]:
        for reference, text in source_spans(document):
            if reference in index:
                raise ValueError("duplicate_original_quote_reference")
            index[reference] = (document["id"], text)
    return index


def reference_payload(payload, bundle):
    """Transmit each character once, with references the model can select."""
    originals, by_text = {}, {}
    for position, (document, original) in enumerate(zip(payload["documents"], bundle["documents"], strict=True)):
        spans = source_spans(original)
        document.pop("content", None)
        document.pop("content_parts", None)
        document["original_quote_refs"] = [reference for reference, _ in spans]
        for reference, text in spans:
            originals[reference] = [position, text]
            by_text[(position, text)] = reference
    for position_and_quote in payload.get("evidence_quotes", {}).values():
        position, quote = position_and_quote
        if reference := by_text.get((position, quote)):
            position_and_quote[1] = reference
    return {**payload, "original_quotes": originals}


def resolve_quotations(value, bundle):
    index = quotation_index(bundle)

    def resolve(item, source_id=None):
        if isinstance(item, list):
            return [resolve(part, source_id) for part in item]
        if not isinstance(item, dict):
            return item
        source_id = item.get("source_id", source_id)
        result = {key: resolve(part, source_id) for key, part in item.items()}
        quote = result.get("quote")
        if isinstance(quote, str) and quote.startswith(PREFIX):
            original = index.get(quote)
            if original is None or original[0] != source_id:
                raise ValueError("unknown_or_wrong_source_quote_reference")
            result["quote"] = original[1]
        return result

    return resolve(value)
