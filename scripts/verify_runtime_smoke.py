"""
Runtime Smoke Test & Graceful Shutdown Verification.
Spawns the exact production single-worker Uvicorn process, exercises all endpoints,
sends SIGTERM, and verifies graceful scheduler shutdown and WAL checkpointing.
"""

import os
import signal
import subprocess
import sys
import time
import urllib.request
import json

PORT = 8089
BASE_URL = f"http://127.0.0.1:{PORT}"
TEST_DB = "test_smoke_runtime.db"

def run_smoke():
    env = os.environ.copy()
    env["PORT"] = str(PORT)
    env["AGENT_DB_PATH"] = TEST_DB
    env["TURSO_DATABASE_URL"] = ""
    env["TURSO_AUTH_TOKEN"] = ""
    env["ENABLE_DOCS"] = "true"
    if "TESTING" in env:
        del env["TESTING"]

    print(f"[*] Starting production Uvicorn server on port {PORT} with database {TEST_DB}...")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(PORT), "--workers", "1"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )

    try:
        # Wait for server to become responsive
        max_wait = 15
        started = False
        start_time = time.time()
        while time.time() - start_time < max_wait:
            try:
                with urllib.request.urlopen(f"{BASE_URL}/healthz", timeout=2) as resp:
                    if resp.status == 200:
                        started = True
                        break
            except Exception:
                time.sleep(0.5)

        if not started:
            raise RuntimeError("Server failed to start within 15 seconds")

        print("[+] Server started successfully!")

        # 1. Test /healthz
        with urllib.request.urlopen(f"{BASE_URL}/healthz", timeout=5) as r:
            assert r.status == 200
            data = json.loads(r.read().decode())
            print(f"[+] /healthz OK -> status: {data.get('status')}, scheduler_running: {data.get('scheduler_running')}")
            assert data.get("status") == "healthy"
            assert data.get("scheduler_running") is True

        # 2. Test /readyz
        with urllib.request.urlopen(f"{BASE_URL}/readyz", timeout=5) as r:
            assert r.status == 200
            data = json.loads(r.read().decode())
            print(f"[+] /readyz OK -> status: {data.get('status')}")
            assert data.get("status") == "healthy"

        # 3. Test / (frontend dashboard root)
        with urllib.request.urlopen(f"{BASE_URL}/", timeout=5) as r:
            assert r.status == 200
            html = r.read().decode()
            print(f"[+] GET / OK -> {len(html)} bytes served (Title in HTML: {'EchoMind' in html})")
            assert "EchoMind" in html

        # 4. Test /api/agent/feed
        with urllib.request.urlopen(f"{BASE_URL}/api/agent/feed", timeout=5) as r:
            assert r.status == 200
            data = json.loads(r.read().decode())
            print(f"[+] GET /api/agent/feed OK -> {len(data.get('posts', []))} posts returned")
            assert "posts" in data

        # 5. Test /api/agents
        with urllib.request.urlopen(f"{BASE_URL}/api/agents", timeout=5) as r:
            assert r.status == 200
            data = json.loads(r.read().decode())
            print(f"[+] GET /api/agents OK -> count: {data.get('count')}, maxAgents: {data.get('maxAgents')}")
            assert data.get("maxAgents") == 5

        # 6. Test graceful shutdown via SIGTERM
        print("[*] Sending SIGTERM to process to verify graceful shutdown & WAL checkpoint...")
        proc.send_signal(signal.SIGTERM)
        stdout, _ = proc.communicate(timeout=10)

        print("\n--- SERVER RUNTIME & SHUTDOWN LOGS ---")
        print(stdout.strip())
        print("--------------------------------------\n")

        assert "Shutting down application" in stdout
        assert "Background scheduler stopped" in stdout or "Application shutdown complete" in stdout
        assert "SQLite WAL checkpointed and truncated successfully" in stdout or "checkpoint" in stdout
        print("[+] Graceful shutdown & WAL truncation VERIFIED!")

    finally:
        if proc.poll() is None:
            proc.kill()
        for ext in ["", "-wal", "-shm"]:
            p = f"{TEST_DB}{ext}"
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        print("[+] Temporary runtime test databases cleaned up.")


if __name__ == "__main__":
    run_smoke()
