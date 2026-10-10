import pytest
from app.langflow import AgentError, extract_reply


def test_extracts_official_v1_run_structure():
    sample = {"outputs": [{"outputs": [{"results": {"message": {"text": "成功回答"}}}]}]}
    assert extract_reply(sample) == "成功回答"


def test_extracts_legacy_data_text():
    sample = {"outputs": [{"outputs": [{"results": {"message": {"data": {"text": "老格式"}}}}]}]}
    assert extract_reply(sample) == "老格式"


def test_rejects_bad_upstream_payload():
    with pytest.raises(AgentError):
        extract_reply({"error": "missing outputs"})
