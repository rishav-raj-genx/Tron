"""
Unified LLM Client providing Primary (Gemini API) and Fallback (Groq API) support.

Preserves the existing application LLM contract:
- generate(system, user) -> str
- generate_structured(system, user, response_format/schema) -> dict
- chat(messages, response_format) -> dict

Order of execution:
1. Gemini API (PRIMARY)
2. Groq API (FALLBACK on Gemini transient error, quota failure, or API failure)
"""

import asyncio
import json
import logging
import random
import re
import urllib.error
import urllib.request
from typing import Any


class ProviderError(Exception):
    """Exception raised when an LLM provider returns an invalid or empty response.
    Used to differentiate provider‑level failures from other runtime errors.
    """
    def __init__(self, message: str, provider: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code


try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

from config.models import GEMINI_MODEL, GROQ_MODEL
from config.settings import settings
from utils.api import sanitize_url_credentials

logger = logging.getLogger(__name__)

# Optional SDK Imports
try:
    from google import genai
    from google.genai import types
    HAS_GENAI_SDK = True
except ImportError:
    HAS_GENAI_SDK = False

try:
    import groq
    from groq import AsyncGroq
    HAS_GROQ_SDK = True
except ImportError:
    HAS_GROQ_SDK = False


def _normalize_gemini_model(model_name: str) -> str:
    """Normalize model names for Google Gemini API compatibility."""
    m = (model_name or "").strip()
    if not m or m == "gemini-2.5-flash":
        return "gemini-2.0-flash"
    return m


def _convert_to_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert standard JSON Schema to Google Gemini OpenAPI upper-case schema format.
    Strips unsupported keys (additionalProperties, json_schema wrapper, strict, name, $schema).
    Converts lower-case types to upper-case ('string' -> 'STRING', 'object' -> 'OBJECT', etc.)
    """
    if not isinstance(schema, dict):
        return schema

    # Unwrap 'json_schema' wrapper if present
    if "json_schema" in schema and isinstance(schema["json_schema"], dict):
        schema = schema["json_schema"].get("schema", schema)

    UNSUPPORTED_GEMINI_KEYS = {"additionalProperties", "strict", "name", "$schema"}
    converted = {}

    for key, value in schema.items():
        if key in UNSUPPORTED_GEMINI_KEYS:
            continue
        if key == "type" and isinstance(value, str):
            converted["type"] = value.upper()
        elif key == "properties" and isinstance(value, dict):
            converted["properties"] = {
                k: _convert_to_gemini_schema(v) for k, v in value.items()
            }
        elif key == "items" and isinstance(value, dict):
            converted["items"] = _convert_to_gemini_schema(value)
        elif isinstance(value, dict):
            converted[key] = _convert_to_gemini_schema(value)
        else:
            converted[key] = value

    return converted


def _clean_and_parse_json(text: str) -> dict[str, Any]:
    """Clean markdown code fences or prefix/suffix text and parse JSON safely."""
    clean = text.strip()
    if "```json" in clean:
        clean = clean.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in clean:
        clean = clean.split("```", 1)[1].split("```", 1)[0].strip()
    
    # Match outermost JSON object if extra text exists
    match = re.search(r"(\{.*\})", clean, re.DOTALL)
    if match:
        clean = match.group(1)

    return json.loads(clean)


def _validate_structured_response(data: Any, response_format: dict[str, Any]) -> dict[str, Any]:
    """Validate provider JSON at the application boundary.

    Gemini may enforce a schema at the provider; Groq's JSON-object mode does
    not. This common validation makes their application contract equivalent
    without claiming identical provider guarantees.
    """
    schema = response_format.get("json_schema", {}).get("schema", response_format)

    def validate(value: Any, definition: dict[str, Any], path: str) -> Any:
        expected = definition.get("type")
        if expected == "object":
            if not isinstance(value, dict):
                raise ValueError(f"{path} must be an object")
            required = definition.get("required", [])
            missing = [field for field in required if field not in value]
            if missing:
                raise ValueError(f"{path} missing required fields: {', '.join(missing)}")
            properties = definition.get("properties", {})
            if definition.get("additionalProperties") is False:
                unexpected = set(value) - set(properties)
                if unexpected:
                    raise ValueError(f"{path} contains unexpected fields: {', '.join(sorted(unexpected))}")
            for field, field_schema in properties.items():
                if field in value:
                    value[field] = validate(value[field], field_schema, f"{path}.{field}")
            return value
        elif expected == "array":
            # Safe array normalization if provider serialized a single URL/string instead of JSON list
            if isinstance(value, str):
                v_str = value.strip()
                if v_str.startswith("[") and v_str.endswith("]"):
                    try:
                        value = json.loads(v_str)
                    except Exception:
                        value = [v_str]
                elif v_str:
                    value = [v_str]
                else:
                    value = []
            if not isinstance(value, list):
                raise ValueError(f"{path} must be an array")
            item_schema = definition.get("items")
            if item_schema:
                for index, item in enumerate(value):
                    value[index] = validate(item, item_schema, f"{path}[{index}]")
            return value
        elif expected == "string":
            if not isinstance(value, str):
                raise ValueError(f"{path} must be a string")
            return value
        elif expected == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"{path} must be a number")
            return value
        elif expected == "boolean":
            if not isinstance(value, bool):
                raise ValueError(f"{path} must be a boolean")
            return value
        return value

    return validate(data, schema, "response")


def _extract_content_from_data(data: dict[str, Any]) -> str:
    """Extract output text from Gemini or Groq response.
    Raises ProviderError if content is missing or blocked.
    """
    # Gemini response handling
    if "candidates" in data:
        candidates = data.get("candidates", [])
        if not candidates:
            raise ProviderError("Gemini response contains no candidates")
        candidate = candidates[0]
        # Check for blocked or safety finish reasons
        finish_reason = candidate.get("finishReason")
        if finish_reason in ("SAFETY", "BLOCKED"):
            raise ProviderError(f"Gemini response blocked by safety policy: {finish_reason}")
        parts = candidate.get("content", {}).get("parts", [])
        if not parts:
            raise ProviderError("Gemini candidate has no content parts")
        text = parts[0].get("text", "")
        if not text:
            raise ProviderError("Gemini candidate text is empty")
        return text
    # Groq/OpenAI style handling
    if "choices" in data and data["choices"]:
        msg = data["choices"][0].get("message", {})
        content = msg.get("content", "")
        if not content:
            raise ProviderError("Groq response contains empty content")
        return content
    raise ProviderError("LLM response did not contain expected 'choices' or 'candidates' content")


class LLMClient:
    """
    Unified LLM Client implementing Gemini Primary with Groq Fallback.
    Transparently normalizes output formats while isolating provider failover.
    """

    def __init__(self, model: str | None = None):
        self.gemini_model = _normalize_gemini_model(settings.gemini_model or GEMINI_MODEL)
        self.groq_model = settings.groq_model or GROQ_MODEL
        # Custom exception for provider-level failures
        self.ProviderError = ProviderError

    async def generate(self, system: str, user: str) -> str:
        """Generate text completion with Gemini primary and Groq fallback."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ]

        # Try Gemini Primary
        try:
            return await self._call_gemini_text(system=system, user=user)
        except Exception as exc:
            logger.warning(f"[LLM] Gemini primary request failed ({sanitize_url_credentials(exc)}). Attempting Groq fallback...")

        # Try Groq Fallback
        try:
            return await self._call_groq_text(messages=messages)
        except Exception as exc:
            logger.error(f"[LLM] Both Gemini and Groq providers failed: {sanitize_url_credentials(exc)}")
            if isinstance(exc, ProviderError):
                raise
            raise ProviderError(f"Both Gemini and Groq providers failed: {sanitize_url_credentials(exc)}") from exc

    async def generate_structured(
        self,
        system: str,
        user: str,
        response_format: dict[str, Any] | None = None,
        schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """
        Generate structured JSON output with schema enforcement.
        Accepts either `response_format` or `schema`.
        """
        target_schema = response_format if response_format is not None else schema
        if target_schema is None:
            raise ValueError("generate_structured requires a 'response_format' or 'schema' dictionary parameter")

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ]

        # Try Gemini Primary
        try:
            raw_text = await self._call_gemini_text(system=system, user=user, target_schema=target_schema, is_json=True)
            return _validate_structured_response(_clean_and_parse_json(raw_text), target_schema)
        except Exception as exc:
            logger.warning(f"[LLM] Gemini primary structured generation failed ({sanitize_url_credentials(exc)}). Attempting Groq fallback...")

        # Try Groq Fallback
        try:
            raw_text = await self._call_groq_text(messages=messages, target_schema=target_schema, is_json=True)
            return _validate_structured_response(_clean_and_parse_json(raw_text), target_schema)
        except Exception as exc:
            logger.error(f"[LLM] Both Gemini and Groq providers failed structured generation: {sanitize_url_credentials(exc)}")
            if isinstance(exc, ProviderError):
                raise
            raise ProviderError(f"Both Gemini and Groq providers failed structured generation: {sanitize_url_credentials(exc)}") from exc

    async def chat(
        self,
        messages: list[dict[str, Any]],
        response_format: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Multi-turn chat completion with optional structured output."""
        is_json = response_format is not None

        # Extract system prompt and user/assistant messages for Gemini
        system_prompt = ""
        chat_contents = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                system_prompt = content
            else:
                gemini_role = "model" if role == "assistant" else "user"
                chat_contents.append({"role": gemini_role, "parts": [{"text": content}]})

        # Try Gemini Primary
        try:
            raw_text = await self._call_gemini_raw(
                system=system_prompt,
                contents=chat_contents if chat_contents else [{"role": "user", "parts": [{"text": ""}]}],
                messages=messages,
                target_schema=response_format,
                is_json=is_json
            )
            if is_json:
                return _validate_structured_response(_clean_and_parse_json(raw_text), response_format or {})
            return {"content": raw_text}
        except Exception as exc:
            logger.warning(f"[LLM] Gemini primary chat failed ({sanitize_url_credentials(exc)}). Attempting Groq fallback...")

        # Try Groq Fallback
        try:
            raw_text = await self._call_groq_text(messages=messages, target_schema=response_format, is_json=is_json)
            if is_json:
                return _validate_structured_response(_clean_and_parse_json(raw_text), response_format or {})
            return {"content": raw_text}
        except Exception as exc:
            logger.error(f"[LLM] Both Gemini and Groq providers failed chat: {sanitize_url_credentials(exc)}")
            if isinstance(exc, ProviderError):
                raise
            raise ProviderError(f"Both Gemini and Groq providers failed chat: {sanitize_url_credentials(exc)}") from exc

    # -------------------------------------------------------------------------
    # Gemini Implementation (PRIMARY)
    # -------------------------------------------------------------------------
    async def _call_gemini_text(
        self,
        system: str,
        user: str,
        target_schema: dict[str, Any] | None = None,
        is_json: bool = False
    ) -> str:
        contents = [{"role": "user", "parts": [{"text": user}]}]
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ]
        return await self._call_gemini_raw(
            system=system,
            contents=contents,
            messages=messages,
            target_schema=target_schema,
            is_json=is_json
        )

    async def _call_gemini_raw(
        self,
        system: str,
        contents: list[dict[str, Any]],
        messages: list[dict[str, Any]] | None = None,
        target_schema: dict[str, Any] | None = None,
        is_json: bool = False
    ) -> str:
        api_key = settings.gemini_api_key.strip()
        model_name = _normalize_gemini_model(self.gemini_model)

        # 1. Try official SDK if key present and SDK available
        if api_key and HAS_GENAI_SDK:
            try:
                client = genai.Client(api_key=api_key)
                config = types.GenerateContentConfig(
                    system_instruction=system if system else None,
                    max_output_tokens=1200,
                    temperature=0.7,
                )
                if is_json:
                    config.response_mime_type = "application/json"
                    if target_schema:
                        gemini_schema = _convert_to_gemini_schema(target_schema)
                        config.response_schema = gemini_schema

                sdk_contents = []
                for item in contents:
                    parts_text = " ".join([p.get("text", "") for p in item.get("parts", [])])
                    sdk_contents.append(parts_text)

                response = await client.aio.models.generate_content(
                    model=model_name,
                    contents=sdk_contents if len(sdk_contents) > 1 else (sdk_contents[0] if sdk_contents else ""),
                    config=config
                )
                if response and response.text:
                    return response.text
                raise ProviderError("Gemini SDK returned empty response")
            except Exception as exc:
                logger.debug(f"[LLM] google-genai SDK call failed, attempting REST fallback: {exc}")

        # 2. REST API Fallback
        effective_key = api_key or "test-key"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={effective_key}"
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": 1200,
                "temperature": 0.7
            }
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if is_json:
            payload["generationConfig"]["responseMimeType"] = "application/json"
            if target_schema:
                payload["generationConfig"]["responseSchema"] = _convert_to_gemini_schema(target_schema)

        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                if HAS_HTTPX:
                    async with httpx.AsyncClient(timeout=45.0) as client:
                        response = await client.post(url, json=payload)
                        response.raise_for_status()
                        data = response.json()
                        return _extract_content_from_data(data)
                else:
                    req = urllib.request.Request(
                        url,
                        data=json.dumps(payload).encode("utf-8"),
                        headers={"Content-Type": "application/json"}
                    )
                    with urllib.request.urlopen(req, timeout=45.0) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                        return _extract_content_from_data(data)
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < max_retries:
                    delay = (2 ** attempt) + random.uniform(0.1, 0.5)
                    logger.warning(f"[LLM] Gemini REST rate limited (429). Retrying in {delay:.2f}s (attempt {attempt + 1}/{max_retries})...")
                    await asyncio.sleep(delay)
                    continue
                raise ProviderError(f"Gemini REST HTTP {exc.code}", provider="Gemini", status_code=exc.code) from exc
            except Exception as exc:
                is_rate_limit = "429" in str(exc) or "ResourceExhausted" in str(exc) or "Too Many Requests" in str(exc)
                if is_rate_limit and attempt < max_retries:
                    delay = (2 ** attempt) + random.uniform(0.1, 0.5)
                    logger.warning(f"[LLM] Gemini rate limited (429). Retrying in {delay:.2f}s (attempt {attempt + 1}/{max_retries})...")
                    await asyncio.sleep(delay)
                    continue
                if isinstance(exc, ProviderError):
                    raise
                raise ProviderError(f"Gemini REST error: {sanitize_url_credentials(exc)}", provider="Gemini") from exc

    # -------------------------------------------------------------------------
    # Groq Implementation (FALLBACK)
    # -------------------------------------------------------------------------
    async def _call_groq_text(
        self,
        messages: list[dict[str, Any]],
        target_schema: dict[str, Any] | None = None,
        is_json: bool = False
    ) -> str:
        api_key = settings.groq_api_key.strip()
        groq_messages = [dict(m) for m in messages]
        if is_json and target_schema:
            schema_def = target_schema.get("json_schema", {}).get("schema", target_schema)
            props = list(schema_def.get("properties", {}).keys())
            props_str = ", ".join([f"'{p}'" for p in props])
            json_instruction = (
                f"\nCRITICAL JSON RESPONSE SCHEMA INSTRUCTIONS:\n"
                f"1. You MUST reply with a valid JSON object containing EXACTLY these property keys: {props_str}.\n"
                f"2. Do NOT include ANY extra or unexpected property keys (such as 'title', 'headline', 'summary', or 'description').\n"
                f"3. All string fields (such as 'post_text' and 'rationale') MUST be single text strings, NOT nested objects or arrays."
            )
            if groq_messages and groq_messages[0].get("role") == "system":
                groq_messages[0] = {
                    "role": "system",
                    "content": str(groq_messages[0]["content"]) + json_instruction
                }
            else:
                groq_messages.insert(0, {"role": "system", "content": "You are a helpful AI news synthesis assistant." + json_instruction})

        # 1. Try official Groq SDK if key present and SDK available
        if api_key and HAS_GROQ_SDK:
            try:
                client = AsyncGroq(api_key=api_key)
                kwargs: dict[str, Any] = {
                    "model": self.groq_model,
                    "messages": groq_messages,
                    "max_tokens": 1200,
                    "temperature": 0.7,
                }
                if is_json:
                    kwargs["response_format"] = {"type": "json_object"}

                response = await client.chat.completions.create(**kwargs)
                if response and response.choices and response.choices[0].message:
                    content = response.choices[0].message.content or ""
                    if not content:
                        raise ProviderError("Groq response contains empty content")
                    return content
            except Exception as exc:
                logger.debug(f"[LLM] Groq SDK call failed, attempting REST fallback: {exc}")

        # 2. REST API Fallback
        effective_key = api_key or "test-key"
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {effective_key}",
            "Content-Type": "application/json"
        }
        payload: dict[str, Any] = {
            "model": self.groq_model,
            "messages": groq_messages,
            "max_tokens": 1200,
            "temperature": 0.7
        }
        if is_json:
            payload["response_format"] = {"type": "json_object"}

        if HAS_HTTPX:
            async with httpx.AsyncClient(timeout=45.0) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
                return _extract_content_from_data(data)
        else:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers
            )
            try:
                with urllib.request.urlopen(req, timeout=45.0) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    return _extract_content_from_data(data)
            except urllib.error.HTTPError as exc:
                raise ProviderError(f"Groq REST HTTP {exc.code}") from exc
            except Exception as exc:
                raise ProviderError(f"Groq REST error: {exc}") from exc
