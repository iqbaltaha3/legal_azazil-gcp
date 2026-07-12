# utils/llm_client.py
"""
Groq-backed LLM client with timeout protection.
"""
import time
import json
from groq import Groq
from config.settings import GROQ_MODEL, GROQ_API_KEY
from utils.metrics import log_llm_call

# Initialize client with timeout
_client = Groq(
    api_key=GROQ_API_KEY,
    timeout=30.0
) if GROQ_API_KEY else None


def _require_client():
    if _client is None:
        raise RuntimeError("GROQ_API_KEY is not set. Export it before running the app.")
    return _client


class GroqClient:
    def __init__(self, model=None):
        self.model = model or GROQ_MODEL

    def generate(self, prompt: str, max_tokens: int = 1024, temperature: float = 0.7,
                 model: str = None, caller: str = "unknown") -> str:
        model_to_use = model or self.model
        client = _require_client()
        start = time.time()
        success = True
        error = None
        content = ""
        usage = None
        try:
            response = client.chat.completions.create(
                model=model_to_use,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=30,                    # ← Added
            )
            content = response.choices[0].message.content or ""
            usage = getattr(response, "usage", None)
        except Exception as e:
            success = False
            error = str(e)
            content = f"LLM error: {e}"
        finally:
            latency_ms = int((time.time() - start) * 1000)
            prompt_tokens = getattr(usage, "prompt_tokens", 0)
            completion_tokens = getattr(usage, "completion_tokens", 0)
            log_llm_call(
                caller=caller, model=model_to_use, mode="generate",
                latency_ms=latency_ms,
                prompt_chars=len(prompt), completion_chars=len(content),
                temperature=temperature, success=success, error=error,
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            )
        return content

    def chat_with_tools(self, messages: list, tools: list, temperature: float = 0.3,
                         caller: str = "agent", max_tokens: int = 1024) -> dict:
        client = _require_client()
        start = time.time()
        success = True
        error = None
        content = ""
        normalized_tool_calls = []
        usage = None
        prompt_chars = sum(len(m.get("content", "") or "") for m in messages)

        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=45,                    # ← Added (longer for tools)
            )
            msg = response.choices[0].message
            content = msg.content or ""
            usage = getattr(response, "usage", None)

            if msg.tool_calls:
                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments)
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    normalized_tool_calls.append({
                        "id": tc.id,
                        "function": {"name": tc.function.name, "arguments": args},
                    })
        except Exception as e:
            success = False
            error = str(e)
            content = f"LLM error: {e}"
        finally:
            latency_ms = int((time.time() - start) * 1000)
            prompt_tokens = getattr(usage, "prompt_tokens", 0)
            completion_tokens = getattr(usage, "completion_tokens", 0)
            log_llm_call(
                caller=caller, model=self.model, mode="chat_with_tools",
                latency_ms=latency_ms,
                prompt_chars=prompt_chars, completion_chars=len(content),
                temperature=temperature, tool_calls_returned=len(normalized_tool_calls),
                success=success, error=error,
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            )

        return {"content": content, "tool_calls": normalized_tool_calls}