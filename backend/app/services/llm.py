import http.client
import json
import time
import urllib.error
import urllib.request

from app.core.config import get_settings


class LLMError(Exception):
    """Raised when the chat-completion API rejects or garbles a request."""


def chat_json(system_prompt: str, user_prompt: str, max_tokens: int = 2000) -> dict:
    """Call the configured OpenAI-compatible chat endpoint and parse a JSON
    object response. Retries transient failures; raises LLMError otherwise."""
    settings = get_settings()
    request = urllib.request.Request(
        f"{settings.llm_base_url}/chat/completions",
        data=json.dumps(
            {
                "model": settings.llm_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0,
                "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
            }
        ).encode(),
        headers={
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        },
    )
    content: str | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                payload = json.load(response)
            content = payload["choices"][0]["message"]["content"]
            break
        except urllib.error.HTTPError as error:
            if error.code < 500 and error.code != 429:
                raise LLMError(f"HTTP {error.code} from chat API") from error
        except (OSError, http.client.HTTPException, ValueError, KeyError, IndexError):
            pass  # transient network/truncation failure, retry below
        if attempt < 2:
            time.sleep(2 * (attempt + 1))
    if content is None:
        raise LLMError("Chat API request failed after 3 attempts")
    try:
        parsed = json.loads(content)
    except ValueError as error:
        raise LLMError(f"Chat API returned non-JSON content: {content[:200]}") from error
    if not isinstance(parsed, dict):
        raise LLMError(f"Chat API returned unexpected JSON: {content[:200]}")
    return parsed
