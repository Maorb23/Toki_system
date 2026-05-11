import json
import requests
from django.conf import settings

class NebiusConfigurationError(RuntimeError):
    """Raised when Nebius is not configured."""

class NebiusRuntimeError(RuntimeError):
    """Raised when Nebius returns an error or invalid response."""

class NebiusLLMClient:
    """
    Thin Nebius OpenAI-compatible chat-completions client.

    There is intentionally no fake fallback here. If configuration is missing,
    the message analysis feature should fail clearly.
    """

    def __init__(self) -> None:
        self.api_key = settings.NEBIUS_API_KEY
        self.base_url = (settings.NEBIUS_BASE_URL or "").rstrip("/")
        self.model = settings.NEBIUS_MODEL

    def _validate_config(self) -> None:
        missing = []
        if not self.api_key:
            missing.append("NEBIUS_API_KEY")
        if not self.base_url:
            missing.append("NEBIUS_BASE_URL")
        if not self.model:
            missing.append("NEBIUS_MODEL")
        if missing:
            raise NebiusConfigurationError(
                "Missing Nebius configuration: " + ", ".join(missing)
            )

    def chat_json(self, *, system_prompt: str, user_prompt: str, temperature: float = 0.2) -> dict:
        self._validate_config()

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=60)
        except requests.RequestException as exc:
            raise NebiusRuntimeError(f"Nebius request failed: {exc}") from exc

        if response.status_code >= 400:
            raise NebiusRuntimeError(
                f"Nebius returned HTTP {response.status_code}: {response.text[:1000]}"
            )

        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise NebiusRuntimeError(f"Unexpected Nebius response shape: {response.text[:1000]}") from exc

        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise NebiusRuntimeError(
                "Nebius returned invalid JSON. Raw content: " + content[:2000]
            ) from exc
