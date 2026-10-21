"""Amazon Bedrock for AI Insights (ai-insights spec §3, §5, §6). The only module that calls Bedrock; it builds the one
request shape the gate's validator accepts. Imported lazily (only providers/live imports aws95.aws)."""
from __future__ import annotations

import json
import re
import threading
from typing import Any

from aws95.aws import gate
from aws95.contracts.insights import InsightSection

MAX_OUTPUT_TOKENS = 1500
TEMPERATURE = 0.2
LEAD = ("Here are aggregate figures for one AWS account. Use only these figures and do not invent numbers.")
_LOCK = threading.Lock()                      # one installed validator per pair: Converse calls run one at a time
_KEYS = {"modelId", "system", "messages", "inferenceConfig"}


def _text_blocks(value: Any) -> bool:
    return (isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict)
            and set(value[0]) == {"text"} and isinstance(value[0]["text"], str))


def check_converse(params) -> None:
    """The only Converse request the app may send. Raises ValueError otherwise (the gate refuses it)."""
    if set(params) != _KEYS or not isinstance(params["modelId"], str) or not params["modelId"]:
        raise ValueError("unexpected Converse parameters")
    if not _text_blocks(params["system"]):
        raise ValueError("system must be one text block")
    messages = params["messages"]
    if not (isinstance(messages, list) and len(messages) == 1 and messages[0].get("role") == "user"
            and set(messages[0]) == {"role", "content"} and _text_blocks(messages[0]["content"])):
        raise ValueError("exactly one user message with one text block")
    if params["inferenceConfig"] != {"maxTokens": MAX_OUTPUT_TOKENS, "temperature": TEMPERATURE}:
        raise ValueError("inference settings are fixed")


def list_models(session, region: str, *, cancel=None) -> list[tuple[str, str]]:
    reply = gate.call(session, "bedrock", "list_foundation_models", region=region, cancel=cancel,
                      byProvider="Anthropic", byOutputModality="TEXT")
    models = {(m["modelId"], m.get("modelName") or m["modelId"]) for m in reply.get("modelSummaries", [])}
    return sorted(models, reverse=True)


def converse(session, region: str, model_id: str, system_prompt: str, entry: dict, *, cancel=None) -> dict:
    params = {"modelId": model_id, "system": [{"text": system_prompt}],
              "messages": [{"role": "user", "content": [{"text": LEAD + "\n\n" + json.dumps(entry, sort_keys=True)}]}],
              "inferenceConfig": {"maxTokens": MAX_OUTPUT_TOKENS, "temperature": TEMPERATURE}}
    with _LOCK, gate.validator("bedrock-runtime", "converse", check_converse):
        reply = gate.call(session, "bedrock-runtime", "converse", region=region, cancel=cancel, **params)
    blocks = reply.get("output", {}).get("message", {}).get("content", [])
    usage = reply.get("usage", {})
    return {"text": "\n".join(b.get("text", "") for b in blocks if "text" in b),
            "input_tokens": int(usage.get("inputTokens", 0)), "output_tokens": int(usage.get("outputTokens", 0))}


_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


def parse_sections(text: str) -> tuple[InsightSection, ...]:
    """Headings (#…, or a line ending with ':') start sections; bulleted lines join the current section; a plain
    line after a bulleted section starts (or joins) a "Notes" section."""
    sections: list[tuple[str, list[str]]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#") or (line.endswith(":") and not _BULLET.match(line)):
            sections.append((line.lstrip("#").strip().rstrip(":"), []))
        elif _BULLET.match(line):
            if not sections:
                sections.append(("Notes", []))
            sections[-1][1].append(_BULLET.sub("", line))
        else:
            if not sections or (sections[-1][0] != "Notes" and sections[-1][1]):
                sections.append(("Notes", []))
            sections[-1][1].append(line)
    return (tuple(InsightSection(t, tuple(b)) for t, b in sections if b)
            or (InsightSection("Notes", (text.strip(),)),))


def explain_error(exc: BaseException, model_id: str) -> str:
    from botocore.exceptions import ClientError
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        message = exc.response.get("Error", {}).get("Message", "")
        if code in ("AccessDeniedException", "AccessDenied", "ResourceNotFoundException"):
            return f"Model access isn't enabled for {model_id} in this account. Request it in the Bedrock console."
        if code in ("ThrottlingException", "ServiceUnavailableException", "ModelNotReadyException"):
            return "Bedrock is busy. Try again in a minute."
        if code == "ValidationException" and "on-demand" in message:
            return f"{model_id} needs an inference profile in this account. Pick another model."
    from aws95.aws.errors import classify
    return classify(exc, "bedrock", "Converse").message
