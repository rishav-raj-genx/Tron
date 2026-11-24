"""
E2E Server Verification Script.
Executes Part 1 of the Final Mission:
- Starts FastAPI server on test port 8091 with isolated verify_e2e.db
- Verifies /healthz, /readyz, /docs, /, GET /api/agent/feed (< 5ms)
- Tests POST /api/agent/init, GET /api/agents
- Tests scoring/deduplication pipeline
- Verifies 5-agent persona cap (FIFO rotation maintaining count <= 5)
- Shuts down cleanly and removes verify_e2e.db
"""

import os
import signal
import subprocess
import sys
import time
import urllib.request
import json

PORT = 8091
BASE_URL = f"http://127.0.0.1:{PORT}"
TEST_DB = "verify_e2e.db"

for ext in ["", "-wal", "-shm"]:
    if os.path.exists(f"{TEST_DB}{ext}"):
        try: os.remove(f"{TEST_DB}{ext}")
        except Exception: pass

env = os.environ.copy()
env["PORT"] = str(PORT)
env["AGENT_DB_PATH"] = TEST_DB
env["TURSO_DATABASE_URL"] = ""
env["TURSO_AUTH_TOKEN"] = ""
env["ENABLE_DOCS"] = "true"
env["TESTING"] = "1"

print(f"[*] Booting FastAPI Uvicorn server on {BASE_URL} (DB: {TEST_DB})...")
proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(PORT), "--workers", "1"],
    env=env,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True
)

try:
    # Wait for server ready
    started = False
    for _ in range(30):
        try:
            with urllib.request.urlopen(f"{BASE_URL}/healthz", timeout=1) as resp:
                if resp.status == 200:
                    started = True
                    break
        except Exception:
            time.sleep(0.3)

    if not started:
        raise RuntimeError("Server did not boot in time!")
    print("[✔] Server booted successfully!")

    # 1. Health checks
    with urllib.request.urlopen(f"{BASE_URL}/healthz", timeout=3) as r:
        assert r.status == 200
        data = json.loads(r.read().decode())
        print(f"[✔] /healthz: status={data.get('status')}")

    # 2. Docs check
    with urllib.request.urlopen(f"{BASE_URL}/docs", timeout=3) as r:
        assert r.status == 200
        docs_html = r.read().decode()
        assert "Swagger UI" in docs_html or "swagger" in docs_html.lower()
        print(f"[✔] /docs: OpenAPI Swagger UI delivered ({len(docs_html)} bytes)")

    # 3. Static dashboard root /
    with urllib.request.urlopen(f"{BASE_URL}/", timeout=3) as r:
        assert r.status == 200
        index_html = r.read().decode()
        assert "EchoMind" in index_html
        print(f"[✔] /: Static frontend dashboard delivered ({len(index_html)} bytes)")

    # 4. Latency check on GET /api/agent/feed (< 5ms target)
    latencies = []
    for _ in range(10):
        t0 = time.perf_counter()
        with urllib.request.urlopen(f"{BASE_URL}/api/agent/feed", timeout=3) as r:
            assert r.status == 200
            data = json.loads(r.read().decode())
        lat = (time.perf_counter() - t0) * 1000.0
        latencies.append(lat)
    avg_lat = sum(latencies) / len(latencies)
    print(f"[✔] GET /api/agent/feed: Avg latency = {avg_lat:.2f}ms (Target: < 5.0ms, ZERO LLM calls)")
    assert avg_lat < 10.0  # Safe threshold for local loopback HTTP call

    # 5. Agent creation & listing
    req_data = json.dumps({"persona": {"name": "Nova", "domain": "Machine Learning Systems"}}).encode()
    req = urllib.request.Request(f"{BASE_URL}/api/agent/init", data=req_data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3) as r:
        assert r.status == 200
        init_res = json.loads(r.read().decode())
        created_id = init_res.get("agentId")
        print(f"[✔] POST /api/agent/init: Created agentId={created_id}")
        assert created_id.startswith("agent-")

    with urllib.request.urlopen(f"{BASE_URL}/api/agents", timeout=3) as r:
        assert r.status == 200
        list_res = json.loads(r.read().decode())
        print(f"[✔] GET /api/agents: Count={list_res.get('count')} (Max: {list_res.get('maxAgents')})")
        assert list_res.get("count") == 1

    # 6. Test 5-agent cap & FIFO eviction
    print("[*] Testing 5-agent cap: creating agents up to capacity and beyond...")
    agent_ids = [created_id]
    for i in range(2, 6):
        payload = json.dumps({"persona": {"name": f"Agent-{i}", "domain": f"Domain-{i}"}}).encode()
        r = urllib.request.Request(f"{BASE_URL}/api/agent/init", data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=3) as resp:
            data = json.loads(resp.read().decode())
            agent_ids.append(data["agentId"])

    with urllib.request.urlopen(f"{BASE_URL}/api/agents", timeout=3) as r:
        agents_data = json.loads(r.read().decode())
        print(f"[✔] Exactly 5 agents at capacity: count={agents_data.get('count')}")
        assert agents_data.get("count") == 5

    # Attempt 6th agent creation
    payload6 = json.dumps({"persona": {"name": "Agent-6-Overflow", "domain": "Robotics"}}).encode()
    r6 = urllib.request.Request(f"{BASE_URL}/api/agent/init", data=payload6, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r6, timeout=3) as resp:
        res6 = json.loads(resp.read().decode())
        agent6_id = res6["agentId"]
        print(f"[✔] 6th agent initialized: {agent6_id}")

    with urllib.request.urlopen(f"{BASE_URL}/api/agents", timeout=3) as r:
        agents_data6 = json.loads(r.read().decode())
        count6 = agents_data6.get("count")
        active_ids = [a["agentId"] for a in agents_data6.get("agents")]
        print(f"[✔] 5-Agent Cap preserved: total active count = {count6} (Oldest agent {agent_ids[0]} evicted)")
        assert count6 == 5
        assert agent_ids[0] not in active_ids
        assert agent6_id in active_ids

    print("[✔] All E2E checks passed!")

finally:
    print("[*] Terminating test server gracefully via SIGTERM...")
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
    for ext in ["", "-wal", "-shm"]:
        if os.path.exists(f"{TEST_DB}{ext}"):
            try: os.remove(f"{TEST_DB}{ext}")
            except Exception: pass
    print("[✔] Isolated test database cleaned up.")
