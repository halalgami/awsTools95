import json

import pytest
from botocore.exceptions import ClientError

from aws95.aws import gate
from aws95.aws.insights import bedrock


class FakeClient:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def converse(self, **kw):
        self.calls.append(("converse", kw))
        if self.error:
            raise self.error
        return self.reply

    def list_foundation_models(self, **kw):
        self.calls.append(("list_foundation_models", kw))
        return {"modelSummaries": [{"modelId": "anthropic.claude-a-v1", "modelName": "Claude A"},
                                   {"modelId": "anthropic.claude-b-v2", "modelName": "Claude B"}]}


class FakeSession:
    def __init__(self, client):
        self.client_obj, self.asked = client, []

    def client(self, name, **kw):
        self.asked.append((name, kw.get("region_name")))
        return self.client_obj


REPLY = {"output": {"message": {"content": [{"text": "## Costs\n- EC2 is the top service.\n- Nothing odd."}]}},
         "usage": {"inputTokens": 321, "outputTokens": 45}}


def test_pairs_are_reviewed_and_billed():
    assert gate.INSIGHTS == {("bedrock", "list_foundation_models"), ("bedrock-runtime", "converse")}
    assert gate.INSIGHTS <= gate.ALLOWED and ("bedrock-runtime", "converse") in gate.VALIDATED
    assert "bedrock-runtime" in gate.BILLED_SERVICES


def test_list_models_filters_and_sorts():
    s = FakeSession(FakeClient())
    assert bedrock.list_models(s, "eu-west-1") == [("anthropic.claude-b-v2", "Claude B"),
                                                   ("anthropic.claude-a-v1", "Claude A")]
    name, kw = s.client_obj.calls[0]
    assert kw == {"byProvider": "Anthropic", "byOutputModality": "TEXT"} and s.asked == [("bedrock", "eu-west-1")]


def test_converse_sends_exactly_the_entry():
    s = FakeSession(FakeClient(REPLY))
    entry = {"label": "Account 1", "resource_counts": {"ec2.instance": 3}}
    out = bedrock.converse(s, "eu-west-1", "anthropic.claude-b-v2", "SYSTEM", entry)
    [(name, kw)] = s.client_obj.calls
    assert kw["modelId"] == "anthropic.claude-b-v2" and kw["system"] == [{"text": "SYSTEM"}]
    assert kw["messages"] == [{"role": "user", "content": [{"text": bedrock.LEAD + "\n\n" +
                                                             json.dumps(entry, sort_keys=True)}]}]
    assert kw["inferenceConfig"] == {"maxTokens": 1500, "temperature": 0.2}
    assert out == {"text": REPLY["output"]["message"]["content"][0]["text"], "input_tokens": 321, "output_tokens": 45}
    assert not gate.is_allowed("bedrock-runtime", "converse")        # validator removed after the call


@pytest.mark.parametrize("bad", [
    {"toolConfig": {}},
    {"messages": [{"role": "user", "content": [{"text": "a"}]}, {"role": "user", "content": [{"text": "b"}]}]},
    {"messages": [{"role": "user", "content": [{"image": {}}]}]},
    {"inferenceConfig": {"maxTokens": 4000, "temperature": 0.2}},
    {"inferenceConfig": {"maxTokens": 1500, "temperature": 1.0}},
    {"system": [{"text": "a"}, {"text": "b"}]},
])
def test_converse_validator_refuses_extras(bad):
    """Review Focus 4."""
    good = {"modelId": "m", "system": [{"text": "s"}], "messages": [{"role": "user", "content": [{"text": "x"}]}],
            "inferenceConfig": {"maxTokens": 1500, "temperature": 0.2}}
    bedrock.check_converse(good)
    with pytest.raises(ValueError):
        bedrock.check_converse({**good, **bad})


def test_parse_sections():
    sections = bedrock.parse_sections("## Costs\n- EC2 leads.\n* Lambda small.\n\nPlain closing line.")
    assert [(s.title, s.bullets) for s in sections] == [("Costs", ("EC2 leads.", "Lambda small.")),
                                                          ("Notes", ("Plain closing line.",))]
    assert bedrock.parse_sections("just text")[0].bullets == ("just text",)


@pytest.mark.parametrize("code, words", [("AccessDeniedException", "Model access isn't enabled"),
                                         ("ThrottlingException", "Bedrock is busy"),
                                         ("ValidationException", "inference profile")])
def test_explain_error(code, words):
    exc = ClientError({"Error": {"Code": code, "Message": "on-demand throughput isn't supported"}}, "Converse")
    assert words in bedrock.explain_error(exc, "anthropic.claude-b-v2")
