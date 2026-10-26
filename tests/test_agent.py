import asyncio
import json
from types import SimpleNamespace

import pytest

from conciliacao.agent import FinReconAgent, ToolExecutionError, build_provider, tools_for_openai


class FakeTools:
    async def list_tools(self):
        return [SimpleNamespace(name="conciliar", description="Executa conciliacao", inputSchema={"type": "object", "properties": {"arquivo_csv": {"type": "string"}}, "required": ["arquivo_csv"]})]

    async def call(self, name, arguments):
        assert name == "conciliar"
        assert arguments == {"arquivo_csv": "bandeira.csv"}
        return {"execucao_id": "abc", "summary": {"divergence_count": 2}}


class FakeCompletions:
    def __init__(self):
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if len(self.requests) == 1:
            call = SimpleNamespace(function=SimpleNamespace(name="conciliar", arguments='{"arquivo_csv":"bandeira.csv"}'), id="call_1")
            message = SimpleNamespace(tool_calls=[call], content=None, model_dump=lambda **kwargs: {"role": "assistant", "tool_calls": []})
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[], content="Foram encontradas 2 divergencias."))])


class FakeOpenAI:
    def __init__(self):
        self.chat = SimpleNamespace(completions=FakeCompletions())


def test_tool_schemas_are_exposed_to_openai_compatible_providers():
    tools = asyncio.run(tools_for_openai(FakeTools()))
    assert tools[0]["function"]["name"] == "conciliar"
    assert tools[0]["function"]["parameters"]["required"] == ["arquivo_csv"]


def test_agent_executes_tool_and_bases_final_answer_on_result():
    client = FakeOpenAI()
    result = asyncio.run(FinReconAgent(client, FakeTools(), model="test-model").run("Concilie bandeira.csv"))
    assert result.answer == "Foram encontradas 2 divergencias."
    assert result.tool_calls == ["conciliar"]
    second_messages = client.chat.completions.requests[1]["messages"]
    assert json.loads(second_messages[-1]["content"])["result"]["summary"]["divergence_count"] == 2


def test_agent_rejects_unknown_or_malformed_tool_calls():
    tools = FakeTools()
    agent = FinReconAgent(FakeOpenAI(), tools, model="test-model")
    with pytest.raises(ToolExecutionError, match="nao permitida"):
        asyncio.run(agent.execute_tool("apagar_banco", "{}"))
    with pytest.raises(ToolExecutionError, match="JSON"):
        asyncio.run(agent.execute_tool("conciliar", "nao-json"))


def test_provider_configuration_supports_gemini_openai_and_compatible(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    _, model = build_provider()
    assert model == "gemini-2.5-flash"
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.test/v1")
    _, model = build_provider()
    assert model is None
