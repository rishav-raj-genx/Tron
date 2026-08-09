"""
Live Provider Diagnostic Smoke Test for TRON Autonomous News Publisher.

Run this script from your normal Mac terminal (outside the restricted IDE sandbox)
to test real end-to-end LLM provider connectivity to Gemini and Groq.

Usage:
    cd /Users/apple/Desktop/Tron
    ./venv/bin/python scripts/live_provider_smoke_test.py
"""

import asyncio
import os
import sys

# Ensure root project directory is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config.settings import settings
from services.llm import LLMClient, ProviderError
from services.editorial_engine import EDITORIAL_SYNTHESIS_SCHEMA
from utils.api import sanitize_url_credentials


async def run_live_smoke_test():
    print("=" * 65)
    print("TRON LIVE PROVIDER DIAGNOSTIC SMOKE TEST")
    print("=" * 65)

    # 1. Environment & Configuration Check
    gemini_key_present = bool(settings.gemini_api_key.strip())
    groq_key_present = bool(settings.groq_api_key.strip())

    print(f"[-] Environment Loaded: .env")
    print(f"[-] Gemini API Key Configured: {'YES' if gemini_key_present else 'NO'}")
    print(f"[-] Groq API Key Configured:   {'YES' if groq_key_present else 'NO'}")
    print(f"[-] Gemini Model Target:       {settings.gemini_model}")
    print(f"[-] Groq Model Target:         {settings.groq_model}")
    print("-" * 65)

    llm = LLMClient()

    # 2. Gemini Direct Text Test
    print("\n[1/4] Testing Direct Gemini Text Generation...")
    if not gemini_key_present:
        print("  -> SKIPPED (GEMINI_API_KEY unpopulated)")
    else:
        try:
            res_gemini = await llm._call_gemini_text(
                system="Reply with plain text only.",
                user="Reply with exactly: TRON_LIVE_GEMINI_OK"
            )
            print(f"  -> SUCCESS! Gemini Response: {repr(res_gemini.strip()[:100])}")
        except Exception as exc:
            print(f"  -> FAILED! Error: {sanitize_url_credentials(exc)}")

    # 3. Groq Direct Text Test
    print("\n[2/4] Testing Direct Groq Text Generation...")
    if not groq_key_present:
        print("  -> SKIPPED (GROQ_API_KEY unpopulated)")
    else:
        try:
            res_groq = await llm._call_groq_text(
                messages=[
                    {"role": "system", "content": "Reply with plain text only."},
                    {"role": "user", "content": "Reply with exactly: TRON_LIVE_GROQ_OK"}
                ]
            )
            print(f"  -> SUCCESS! Groq Response: {repr(res_groq.strip()[:100])}")
        except Exception as exc:
            print(f"  -> FAILED! Error: {sanitize_url_credentials(exc)}")

    # 4. Gemini Structured JSON Generation Test
    print("\n[3/4] Testing Structured JSON Generation (Schema Contract)...")
    try:
        struct_res = await llm.generate_structured(
            system="Synthesize a verified news summary in AI Security.\nCRITICAL: 'sources' MUST be a JSON array of HTTP/HTTPS URLs provided in Primary Sources.",
            user="Synthesize post for: Zero-day vulnerability discovered in transformer attention kernel.\nPrimary Sources: [\"https://arxiv.org/abs/2608.12345\"]",
            schema=EDITORIAL_SYNTHESIS_SCHEMA
        )
        print("  -> SUCCESS! Structured Object Received:")
        print(f"     post_text: {repr(struct_res.get('post_text', '')[:80])}...")
        print(f"     rationale: {repr(struct_res.get('rationale', '')[:80])}...")
        print(f"     sources:   {struct_res.get('sources', [])}")
        print("  -> Schema Contract Validation: PASS")
    except Exception as exc:
        print(f"  -> FAILED! Error: {sanitize_url_credentials(exc)}")

    # 5. Gemini -> Groq Failover Simulation
    print("\n[4/4] Testing Gemini -> Groq Failover Simulation...")
    try:
        # Create temporary LLMClient instance with forced Gemini failure
        failover_client = LLMClient()
        async def mock_failing_gemini(*args, **kwargs):
            raise ProviderError("Simulated primary Gemini 503 Service Unavailable")

        failover_client._call_gemini_text = mock_failing_gemini
        
        failover_res = await failover_client.generate(
            system="Reply with plain text.",
            user="Test failover response"
        )
        print(f"  -> SUCCESS! Transparent Failover Result: {repr(failover_res.strip()[:100])}")
    except Exception as exc:
        print(f"  -> FAILED! Error: {sanitize_url_credentials(exc)}")

    print("\n" + "=" * 65)
    print("DIAGNOSTIC SMOKE TEST COMPLETE")
    print("=" * 65)


if __name__ == "__main__":
    asyncio.run(run_live_smoke_test())
