"""Preserve a complete briefing in one Slack message using bounded sections."""

import re

SECTION_LIMIT = 3000


def blocks(text):
    # Slack can split long top-level text into separate posts. Section blocks keep
    # the full main narrative together; no short notification text replaces it.
    paragraphs = re.findall(r'.+?(?:\n\n+|\Z)', text, flags=re.S)
    pieces = []
    for paragraph in paragraphs:
        pieces.extend([paragraph] if len(paragraph) <= SECTION_LIMIT else paragraph.splitlines(keepends=True))
    chunks = []
    for piece in pieces:
        if len(piece) > SECTION_LIMIT:
            raise ValueError('brief_slack_section_too_long')
        if not chunks or len(chunks[-1])+len(piece) > SECTION_LIMIT:
            chunks.append(piece)
        else:
            chunks[-1] += piece
    if not chunks or len(chunks) > 50 or any(not chunk.strip() for chunk in chunks):
        raise ValueError('brief_slack_blocks_invalid')
    assert ''.join(chunks) == text
    return [{'type': 'section', 'text': {'type': 'mrkdwn', 'text': chunk}, 'expand': True}
            for chunk in chunks]
