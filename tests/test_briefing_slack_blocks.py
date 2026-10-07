from copy import deepcopy

import pytest

from quant_company.briefing.editor import render
from quant_company.briefing.slack_blocks import blocks

from .test_briefing import bundle, proposal


def test_long_main_keeps_every_paragraph_and_citation_in_one_slack_message():
    paragraphs = [f"*이슈 {index}*\n" + "시장 변화와 반대 근거를 함께 확인합니다. " * 30
                  + f"<https://example.com/original/{index}|[{index}]>\n\n" for index in range(16)]
    text = ''.join(paragraphs)
    assert len(text) > 8000
    sections = blocks(text)
    assert ''.join(section['text']['text'] for section in sections) == text
    assert len(sections) < 50 and all(len(section['text']['text']) <= 3000 for section in sections)
    assert all(any(paragraph in section['text']['text'] for section in sections) for paragraph in paragraphs)
    assert all(section['expand'] for section in sections)


@pytest.mark.parametrize('text', ['', ' ' * 5, 'x' * 3001])
def test_unrenderable_brief_fails_before_external_delivery(text):
    with pytest.raises(ValueError):
        blocks(text)


def test_full_multi_topic_body_is_checked_against_actual_blocks_before_delivery():
    b, p = bundle(), proposal()
    first = p.issues[0]
    p.issues = []
    for i in range(6):
        issue = deepcopy(first)
        for c in [issue.fact, issue.interpretation, issue.analysis.mechanism,
                  issue.analysis.alternative, issue.next_check]:
            c.id += str(i)
            c.text = ('시장 변화와 경제적 의미를 설명하는 합성 검증 문장입니다. ' * 10)[:380]
        p.issues.append(issue)
    parts, _ = render(p, b)
    assert 9500 < len(parts[0]) < 30000
    sections = blocks(parts[0])
    assert ''.join(s['text']['text'] for s in sections) == parts[0]
    assert len(sections) <= 50
    assert all(len(s['text']['text']) <= 3000 for s in sections)
    # A wire-valid body is not itself a readability or content-quality pass.
    assert len(p.issues) == 6


def test_first_basis_point_unit_is_explained_without_rewriting_stored_facts():
    b, p = bundle(), proposal()
    p.issues[0].fact.text = '국채금리는 6bp 상승했고 단기물은 2bp 상승했다.'
    original = p.model_dump_json()
    main = render(p, b)[0][0]
    assert '6bp(1bp=0.01%포인트)' in main and '2bp 상승' in main
    assert main.count('1bp=0.01%포인트') == 1
    assert p.model_dump_json() == original


def test_existing_basis_point_explanation_is_not_inserted_twice_or_moved_to_next_rate():
    b, p = bundle(), proposal()
    p.issues[0].fact.text = '국채금리는 6bp(1bp=0.01%포인트) 상승했고 단기물은 2bp 상승했다.'
    original = p.model_dump_json()
    main = render(p, b)[0][0]
    assert '6bp(1bp=0.01%포인트) 상승' in main and '2bp 상승' in main
    assert main.count('1bp=0.01%포인트') == 1
    assert p.model_dump_json() == original
