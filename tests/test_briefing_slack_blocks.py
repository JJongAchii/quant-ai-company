import pytest

from quant_company.briefing.slack_blocks import blocks


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
