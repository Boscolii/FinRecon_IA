import asyncio
import json
import copy
from decimal import Decimal
from types import SimpleNamespace

import pytest

from conciliacao.agent import FinReconAgent, McpTools, TokenUsage, ToolExecutionError, build_provider, tools_for_openai
from conciliacao.generator import generate


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
        self.requests.append(copy.deepcopy(kwargs))
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
    assert model == "gemini-3.5-flash-lite"
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.test/v1")
    _, model = build_provider()
    assert model is None


def test_gemini_tool_result_is_returned_to_model():
    class FakeGemini:
        kind = "gemini_native"

        def __init__(self):
            self.requests = []

        def create(self, model, contents, tools):
            self.requests.append(copy.deepcopy(contents))
            if len(self.requests) == 1:
                return {"usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 10, "thoughtsTokenCount": 5, "totalTokenCount": 115}, "candidates": [{"content": {"role": "model", "parts": [{"functionCall": {"name": "conciliar", "args": {"arquivo_csv": "bandeira.csv"}, "id": "call-1"}, "thoughtSignature": "signature"}]}}]}
            return {"usageMetadata": {"promptTokenCount": 150, "candidatesTokenCount": 20, "totalTokenCount": 170}, "candidates": [{"content": {"parts": [{"text": "Foram encontradas 2 divergencias."}]}}]}

    client = FakeGemini()
    result = asyncio.run(FinReconAgent(client, FakeTools(), model="test-model").run("Concilie bandeira.csv"))
    assert result.answer == "Foram encontradas 2 divergencias."
    assert client.requests[1][1]["parts"][0]["thoughtSignature"] == "signature"
    output = client.requests[1][-1]["parts"][0]["functionResponse"]
    assert output["id"] == "call-1"
    assert output["response"]["result"]["summary"]["divergence_count"] == 2
    assert result.usage.model_calls == 2
    assert result.usage.input_tokens == 250
    assert result.usage.output_tokens == 30
    assert result.usage.thinking_tokens == 5
    assert result.usage.total_tokens == 285
    assert result.usage.gemini_standard_uncached_usd("gemini-3.5-flash-lite") == Decimal("0.0001625")


def test_usage_missing_from_one_model_call_is_not_priced_as_zero():
    usage = TokenUsage()
    usage.add_gemini({"promptTokenCount": 100, "totalTokenCount": 100})
    usage.add_gemini(None)
    assert usage.reported_calls == 1 and usage.model_calls == 2
    assert not usage.complete
    assert usage.gemini_standard_uncached_usd("gemini-3.5-flash-lite") is None


def test_openai_usage_is_counted_without_double_counting_reasoning():
    usage = TokenUsage()
    usage.add_openai(SimpleNamespace(prompt_tokens=50, completion_tokens=20, total_tokens=70, prompt_tokens_details=SimpleNamespace(cached_tokens=10)))
    assert (usage.input_tokens, usage.output_tokens, usage.cached_tokens, usage.total_tokens) == (50, 20, 10, 70)
    assert usage.complete


def test_agent_uses_real_mcp_with_synthetic_data(tmp_path, monkeypatch):
    data = tmp_path / "data"
    generate(data / "banco.db", data / "bandeira.csv", data / "gabarito.json", n=40, per_type=1, benign=1, seed=3)
    monkeypatch.setenv("CONCILIACAO_DATA_DIR", str(data))
    monkeypatch.setenv("CONCILIACAO_DB", str(data / "banco.db"))
    monkeypatch.setenv("CONCILIACAO_RUNS", str(tmp_path / "runs"))
    monkeypatch.setenv("CONCILIACAO_LOG", str(tmp_path / "logs" / "audit.jsonl"))

    class FakeGemini:
        kind = "gemini_native"

        def __init__(self):
            self.result = None

        def create(self, model, contents, tools):
            if len(contents) == 1:
                return {"candidates": [{"content": {"role": "model", "parts": [{"functionCall": {"name": "conciliar", "args": {"arquivo_csv": "bandeira.csv", "data": "2026-10-19"}}}]}}]}
            self.result = contents[-1]["parts"][0]["functionResponse"]["response"]
            return {"candidates": [{"content": {"parts": [{"text": "Conciliacao concluida."}]}}]}

    async def run():
        client = FakeGemini()
        async with McpTools(__import__("pathlib").Path(__file__).resolve().parent.parent) as tools:
            result = await FinReconAgent(client, tools, model="simulado").run("Concilie o arquivo")
        assert result.tool_calls == ["conciliar"]
        assert client.result["ok"] is True
        assert client.result["result"]["summary"]["divergence_count"] == 6
        assert len(client.result["result"]["execucao_id"]) == 64

    asyncio.run(run())
