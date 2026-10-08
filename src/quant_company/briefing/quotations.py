"""Input-bound references to complete, unchanged original text."""

import hashlib
import json
import re

QUOTE_REFERENCE_VERSION = 1
PREFIX = "@original:"
COMPACT_PREFIX = "@q:"
CANDIDATE_PREFIX = "@candidate:"


def quote_parts(value):
    return [value] if isinstance(value, str) else value


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
    originals, by_text, by_source = {}, {}, {}
    for position, (document, original) in enumerate(zip(payload["documents"], bundle["documents"], strict=True)):
        spans = source_spans(original)
        document.pop("content", None)
        document.pop("content_parts", None)
        document["original_quote_refs"] = [reference for reference, _ in spans]
        for reference, text in spans:
            originals[reference] = [position, text]
            by_text[(position, text)] = reference
            by_source[(original['id'], text)] = reference
    for position_and_quote in payload.get("evidence_quotes", {}).values():
        position, quote = position_and_quote
        if reference := by_text.get((position, quote)):
            position_and_quote[1] = reference
    def compact(item, source_id=None):
        if isinstance(item, list):
            return [compact(part, source_id) for part in item]
        if not isinstance(item, dict):
            return item
        source_id = item.get('source_id', source_id)
        result = {key: compact(part, source_id) for key, part in item.items()}
        quote = result.get('quote')
        if isinstance(quote, (str, list)):
            parts = [by_source.get((source_id, part), part) for part in quote_parts(quote)]
            result['quote'] = parts[0] if isinstance(quote, str) else parts
        return result

    # Repair protection retains every prior claim and quote on the server. Its
    # repeated spans need the same lossless references as the original bodies;
    # recursively copy, never mutate the frozen bundle or the raw receipts.
    return {**compact(payload), "original_quotes": originals}


def resolve_quotations(value, bundle):
    index = quotation_index(bundle)
    index.update({COMPACT_PREFIX+str(i+1): original for i, original in enumerate(list(index.values()))})

    reference = r"(?:@q:[1-9][0-9]*|@original:[0-9a-f]{20})"
    references = re.compile(reference + r"(?:\s*[;,]\s*" + reference + r")*")

    def resolve_quote(quote, source_id):
        if not isinstance(quote, (str, list)):
            return quote  # Domain schema reports the invalid type.
        parts = []
        for part in quote_parts(quote):
            if isinstance(part, str) and part.startswith((PREFIX, COMPACT_PREFIX)):
                if not references.fullmatch(part):
                    raise ValueError("unknown_or_wrong_source_quote_reference")
                aliases = re.split(r"\s*[;,]\s*", part)
                for alias in aliases:
                    original = index.get(alias)
                    if original is None or original[0] != source_id:
                        raise ValueError("unknown_or_wrong_source_quote_reference")
                    parts.append(original[1])
            else:
                parts.append(part)
        if not 1 <= len(parts) <= 4 or any(parts.count(p) > 1 for p in parts):
            raise ValueError("invalid_quote_fragments")
        return parts[0] if isinstance(quote, str) and len(parts) == 1 else parts

    def resolve(item, source_id=None):
        if isinstance(item, list):
            return [resolve(part, source_id) for part in item]
        if not isinstance(item, dict):
            return item
        source_id = item.get("source_id", source_id)
        result = {key: resolve_quote(part, source_id) if key == "quote" else resolve(part, source_id)
                  for key, part in item.items()}
        # Claim evidence keeps its established scalar schema and overall four
        # entry limit. Fact inventories retain arrays, including disjoint spans.
        if isinstance(result.get("evidence"), list):
            result["evidence"] = [
                {**entry, "quote": part} for entry in result["evidence"]
                for part in quote_parts(entry["quote"])]
        return result

    return resolve(value)


def resolve_candidate_requests(value, bundle):
    selected = {d['id'] for d in bundle['documents']}
    aliases = {CANDIDATE_PREFIX+str(i): d['id'] for i, d in enumerate(bundle.get('candidate_documents', []))
               if d['id'] not in selected}
    value = {**value, 'source_requests': [dict(row) for row in value.get('source_requests', [])]}
    for row in value['source_requests']:
        identity = row.get('source_id')
        if isinstance(identity, str) and identity.startswith(CANDIDATE_PREFIX):
            if identity not in aliases:
                raise ValueError('unknown_candidate_source_reference')
            row['source_id'] = aliases[identity]
    return value


def ordered_reference_payload(payload):
    """Remove the duplicate reference lists, retaining every ordered text span."""
    return {**payload, 'documents': [{k: v for k, v in d.items() if k != 'original_quote_refs'}
                                    for d in payload['documents']],
            'original_quote_layout': 'ordered_rows',
            'original_quotes': [[reference, *value] for reference, value in payload['original_quotes'].items()]}


PROMPT_LIMIT = 88000  # Margin under ProviderRequest.prompt max_length=90000.
REFERENCE = re.compile(r"@q:[1-9][0-9]*|@original:[0-9a-f]{20}")


def fit_original_rows(header, payload, quotes=(), limit=PROMPT_LIMIT):
    """Only when the complete originals exceed the input limit: keep every row that holds a cited or
    committed quote, then the remaining rows in source order while they fit. Row IDs never change, so
    server-side resolution is unaffected. Returns (prompt, trimmed) or raises brief_context_limit."""
    encode = lambda value: header + json.dumps(value, ensure_ascii=False, separators=(",", ":"))  # noqa: E731
    result = encode(payload)
    if len(result) <= limit:
        return result, []
    rows = payload.get("original_quotes")
    grouped = payload.get("original_quote_layout") == "grouped_documents"
    if grouped:
        flat = [(position, f"@q:{row[0]}", row[1]) for position, group in enumerate(rows) for row in group]
    elif isinstance(rows, list):
        flat = [(row[1], row[0], row[2]) for row in rows]
    else:
        raise ValueError("brief_context_limit")
    outside = json.dumps({k: v for k, v in payload.items() if k != "original_quotes"}, ensure_ascii=False)
    cited = set(REFERENCE.findall(outside))
    texts = [" ".join(q.split()) for q in quotes if isinstance(q, str) and not REFERENCE.fullmatch(q)]

    def needed(text):
        plain = " ".join(text.split())
        # A quote may span adjacent rows: keep the rows holding its start, its end and its middle.
        return any(q in plain or plain in q or q[:40] in plain or q[-40:] in plain for q in texts if q)

    keep = {i for i, (_, key, text) in enumerate(flat) if key in cited or needed(text)}

    def build(selected):
        if grouped:
            groups = [[] for _ in rows]
            for i in sorted(selected):
                position, key, text = flat[i]
                groups[position].append([int(key.removeprefix("@q:")), text])
            kept_rows = groups
        else:
            kept_rows = [rows[i] for i in sorted(selected)]
        trimmed = {}
        for i, (position, _, text) in enumerate(flat):
            if i not in selected:
                count, chars = trimmed.get(position, (0, 0))
                trimmed[position] = (count + 1, chars + len(text))
        report = [[position, count, chars] for position, (count, chars) in sorted(trimmed.items())]
        return {**payload, "original_quotes": kept_rows, "trimmed_originals": report}, report

    candidate, report = build(keep)
    if len(encode(candidate)) > limit:
        raise ValueError("brief_context_limit")
    for i in range(len(flat)):  # Fill the remaining budget in source order.
        if i in keep:
            continue
        trial, trial_report = build(keep | {i})
        if len(encode(trial)) <= limit:
            keep.add(i)
            candidate, report = trial, trial_report
    return encode(candidate), report


def compact_reference_payload(payload, bundle):
    """Short bound identities and ranges; originals, main text and discovery titles remain complete."""
    aliases = {row[0]: COMPACT_PREFIX+str(i+1) for i, row in enumerate(payload['original_quotes'])}

    def compact(value):
        if isinstance(value, dict):
            return {key: compact_quote(item) if key == 'quote' else compact(item)
                    for key, item in value.items()}
        if isinstance(value, list):
            return [compact(item) for item in value]
        return value

    def compact_quote(value):
        if isinstance(value, str):
            return aliases.get(value, value)
        if isinstance(value, list):
            return [compact_quote(part) for part in value]
        return value

    result = compact(payload)
    result['original_quotes'] = [[aliases[reference], position, text]
                                 for reference, position, text in payload['original_quotes']]
    result['evidence_quotes'] = {key: [position, aliases.get(quote, quote)]
                                for key, (position, quote) in payload.get('evidence_quotes', {}).items()}
    candidates = {d['id']: CANDIDATE_PREFIX+str(i) for i, d in enumerate(bundle.get('candidate_documents', []))}
    result['unselected_source_index'] = [[candidates.get(row[0], row[0]), *row[1:]]
                                         for row in result.get('unselected_source_index', [])]
    documents = {d['id']: d for d in bundle['documents']}
    for context in result.get('market_context', []):
        original = documents.get(context.get('source_id'), {}).get('content', '')
        text = context.get('text')
        start = original.find(text) if isinstance(text, str) else -1
        if start >= 0:
            context['original_text_range'] = [start, start+len(text)]
            del context['text']
    result['original_quote_layout'] = 'compact_ordered_rows'
    return result
