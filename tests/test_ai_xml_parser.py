import pytest
import re
from cogs.ai import _parse_xml_tool_calls


def test_parse_xml_tool_calls_invoke_format():
    text = """
Some text before the tool call.
<invoke name="web_search">
<parameter name="query" string="True">SpaceX news</parameter>
</invoke>
Some text after.
"""
    calls = _parse_xml_tool_calls(text)
    assert len(calls) == 1
    assert calls[0][0] == "web_search"
    assert calls[0][1] == {"query": "SpaceX news"}


def test_parse_xml_tool_calls_mimo_format():
    text = """
Some text before.
<tool_call>
<function=web_fetch>
<parameter=url>https://example.com</parameter>
<parameter=maxChars>5000</parameter>
</function>
</tool_call>
Some text after.
"""
    calls = _parse_xml_tool_calls(text)
    assert len(calls) == 1
    assert calls[0][0] == "web_fetch"
    # Convert parameters to stripped values
    assert calls[0][1] == {"url": "https://example.com", "maxChars": "5000"}


def test_strip_singular_tool_call_tags():
    text = """
Some text before.
<tool_call>
<function=web_fetch>
<parameter=url>https://example.com</parameter>
</function>
</tool_call>
Some text after.
"""
    cleaned = re.sub(r"<tool_call>.*?</tool_call>", "", text, flags=re.DOTALL).strip()
    assert "<tool_call>" not in cleaned
    assert "web_fetch" not in cleaned
    assert "https://example.com" not in cleaned
    assert cleaned == "Some text before.\n\nSome text after."


def test_strip_vision_content_helper():
    from cogs.ai import AICog
    payload = [
        {"role": "user", "content": [
            {"type": "text", "text": "hello"},
            {"type": "image_url", "image_url": {"url": "base64_data"}}
        ]},
        {"role": "assistant", "content": "hi there"}
    ]
    cleaned = AICog._strip_vision_content(payload)
    assert cleaned[0]["content"] == "hello"
    assert cleaned[1]["content"] == "hi there"
