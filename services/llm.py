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

import json
import logging
import re
from typing import Any

import httpx

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
    not.  This common validation makes their application contract equivalent
    without claiming identical provider guarantees.
    """
    schema = response_format.get("json_schema", {}).get("schema", response_format)

    def validate(value: Any, definition: dict[str, Any], path: str) -> None:
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
                    validate(value[field], field_schema, f"{path}.{field}")
        elif expected == "array":
            if not isinstance(value, list):
                raise ValueError(f"{path} must be an array")
            item_schema = definition.get("items")
            if item_schema:
                for index, item in enumerate(value):
                    validate(item, item_schema, f"{path}[{index}]")
        elif expected == "string" and not isinstance(value, str):
            raise ValueError(f"{path} must be a string")
        elif expected == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            raise ValueError(f"{path} must be a number")
        elif expected == "boolean" and not isinstance(value, bool):
            raise ValueError(f"{path} must be a boolean")

    validate(data, schema, "response")
    return data


def _extract_content_from_data(data: dict[str, Any]) -> str:
    """Extract output text from either OpenAI/Groq 'choices' or Gemini 'candidates' structure."""
    if "choices" in data and data["choices"]:
        msg = data["choices"][0].get("message", {})
        return msg.get("content", "")
    elif "candidates" in data and data["candidates"]:
        parts = data["candidates"][0].get("content", {}).get("parts", [])
        if parts:
            return parts[0].get("text", "")
    raise ValueError("LLM response did not contain expected 'choices' or 'candidates' content")


class LLMClient:
    """
    Unified LLM Client implementing Gemini Primary with Groq Fallback.
    Transparently normalizes output formats while isolating provider failover.
    """

    def __init__(self, model: str | None = None):
        self.gemini_model = settings.gemini_model or GEMINI_MODEL
        self.groq_model = settings.groq_model or GROQ_MODEL

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
            raise

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
            raise

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
            raise

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
                        config.response_json_schema = target_schema.get("json_schema", {}).get("schema", target_schema)

                sdk_contents = []
                for item in contents:
                    parts_text = " ".join([p.get("text", "") for p in item.get("parts", [])])
                    sdk_contents.append(parts_text)

                response = await client.aio.models.generate_content(
                    model=self.gemini_model,
                    contents=sdk_contents if len(sdk_contents) > 1 else (sdk_contents[0] if sdk_contents else ""),
                    config=config
                )
                if response and response.text:
                    return response.text
            except Exception as exc:
                logger.debug(f"[LLM] google-genai SDK call failed, attempting REST fallback: {exc}")

        # 2. REST API Fallback
        effective_key = api_key or "test-key"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent?key={effective_key}"
        payload: dict[str, Any] = {
            "model": self.gemini_model,
            "messages": messages or [],
            "contents": contents,
            "max_tokens": 1200,
            "generationConfig": {
                "maxOutputTokens": 1200,
                "temperature": 0.7
            }
        }
        if system:
            payload["system_instruction"] = {"parts": [{"text": system}]}
        if is_json:
            payload["generationConfig"]["responseMimeType"] = "application/json"
            if target_schema:
                payload["generationConfig"]["responseSchema"] = target_schema.get("json_schema", {}).get("schema", target_schema)

        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            return _extract_content_from_data(data)

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

        # 1. Try official Groq SDK if key present and SDK available
        if api_key and HAS_GROQ_SDK:
            try:
                client = AsyncGroq(api_key=api_key)
                kwargs: dict[str, Any] = {
                    "model": self.groq_model,
                    "messages": messages,
                    "max_tokens": 1200,
                    "temperature": 0.7,
                }
                if is_json:
                    # Groq guarantees JSON syntax here, not provider-level schema
                    # enforcement; _validate_structured_response enforces ours.
                    kwargs["response_format"] = {"type": "json_object"}

                response = await client.chat.completions.create(**kwargs)
                if response and response.choices and response.choices[0].message:
                    return response.choices[0].message.content or ""
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
            "messages": messages,
            "max_tokens": 1200,
            "temperature": 0.7
        }
        if is_json:
            payload["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            return _extract_content_from_data(data)
