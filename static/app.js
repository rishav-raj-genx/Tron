/**
 * EchoMind Autonomous News Publisher — Frontend Client Layer
 * Multi-Agent Dashboard, Responsive Cards, Dark/Light Theme System,
 * Server-Side 5-Agent Limit Confirmation Modal, and Health Status Management.
 */

// ============================================================================
// 1. CENTRAL API BASE URL RESOLUTION
// ============================================================================
// Priority:
// 1. Explicit URL parameter: ?api=https://...
// 2. Explicit window global: window.__API_BASE_URL__
// 3. Same-origin relative path ("") if running inside browser on host
// 4. Default fallback: https://echomind-ltwo.onrender.com
const getBaseUrl = () => {
  try {
    const urlParams = new URLSearchParams(window.location.search);
    const paramApi = urlParams.get("api");
    if (paramApi) return paramApi.replace(/\/$/, "");
    if (window.__API_BASE_URL__) return window.__API_BASE_URL__.replace(/\/$/, "");
    if (window.location && window.location.origin && window.location.origin.startsWith("http")) {
      // When served directly from backend (Render or localhost), relative path "" is 100% reliable
      return "";
    }
  } catch (e) {
    console.warn("[EchoMind] Could not parse URL params:", e);
  }
  return "https://echomind-ltwo.onrender.com";
};

const API_BASE_URL = getBaseUrl();
console.log("[EchoMind Client] Active API Base URL:", API_BASE_URL || "(same-origin)");

// ============================================================================
// 2. STATE MANAGEMENT & STORAGE
// ============================================================================
const STATE = {
  agentId: localStorage.getItem("echomind_agent_id") || "",
  personaName: localStorage.getItem("echomind_persona_name") || "",
  personaDomain: localStorage.getItem("echomind_persona_domain") || "",
  theme: localStorage.getItem("echomind_theme") || "dark",
  statusData: null,
  posts: [],
  agents: [],
  maxAgents: 5,
  publishWindowMinutes: 120,
  backendHealthy: false,
  pendingInitPayload: null
};

// ============================================================================
// 3. API SERVICE LAYER
// ============================================================================
async function apiRequest(endpoint, options = {}) {
  const cleanEndpoint = endpoint.startsWith("/") ? endpoint : `/${endpoint}`;
  const url = API_BASE_URL ? `${API_BASE_URL}${cleanEndpoint}` : cleanEndpoint;

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), options.timeout || 35000);

  const headers = { ...(options.headers || {}) };
  // Only set Content-Type for requests with a body to prevent unnecessary CORS preflight on GET requests
  if (options.body && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }

  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal,
      headers
    });
    clearTimeout(timeoutId);

    if (!response.ok) {
      const errorBody = await response.text();
      throw new Error(`HTTP ${response.status}: ${errorBody.slice(0, 100)}`);
    }

    return await response.json();
  } catch (err) {
    clearTimeout(timeoutId);
    if (err.name === "AbortError") {
      throw new Error("Backend request timed out.");
    }
    throw err;
  }
}

const apiClient = {
  async checkHealth() {
    try {
      return await apiRequest("/healthz", { timeout: 10000 });
    } catch (e) {
      return await apiRequest("/health", { timeout: 10000 });
    }
  },

  async getAgents(activeAgentId = "") {
    return await apiRequest(`/api/agents?activeAgentId=${encodeURIComponent(activeAgentId)}`, { timeout: 15000 });
  },

  async initAgent(name, domain) {
    return await apiRequest("/api/agent/init", {
      method: "POST",
      body: JSON.stringify({
        persona: {
          name: name.trim(),
          domain: domain.trim()
        }
      })
    });
  },

  async getStatus(agentId) {
    return await apiRequest(`/api/agent/status?agentId=${encodeURIComponent(agentId)}`);
  },

  async getFeed(agentId) {
    return await apiRequest(`/api/agent/feed?agentId=${encodeURIComponent(agentId)}`);
  }
};

// ============================================================================
// 4. THEME & CADENCE UI HELPERS
// ============================================================================
function formatCadenceLabel(minutes) {
  const m = Number(minutes) || 120;
  if (m >= 60 && m % 60 === 0) {
    const hours = m / 60;
    return `${hours}-Hour Window Cadence`;
  }
  return `${m}-Minute Window Cadence`;
}

function updateWindowCadenceUI(minutes) {
  if (!minutes) return;
  STATE.publishWindowMinutes = Number(minutes);
  const label = document.getElementById("brand-cadence-label");
  if (label) {
    label.textContent = formatCadenceLabel(STATE.publishWindowMinutes);
  }
  const feedEmpty = document.getElementById("feed-empty-state");
  if (feedEmpty && (!STATE.posts || STATE.posts.length === 0)) {
    feedEmpty.textContent = `No stories published to feed yet. At window close (${STATE.publishWindowMinutes} min), the highest-scoring verified leader meeting the minimum threshold (75.0) will be published.`;
  }
}

function initTheme() {
  let savedTheme = "dark";
  try {
    savedTheme = localStorage.getItem("echomind_theme");
  } catch (e) {
    console.warn("Storage access:", e);
  }
  const prefersLight = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
  const initialTheme = savedTheme || (prefersLight ? "light" : "dark");
  setTheme(initialTheme);
}

function setTheme(theme) {
  STATE.theme = theme;
  document.documentElement.setAttribute("data-theme", theme);
  try {
    localStorage.setItem("echomind_theme", theme);
  } catch (e) {
    console.warn("Storage save:", e);
  }

  const toggleBtn = document.getElementById("theme-toggle-btn");
  const moonIcon = document.getElementById("theme-icon-moon");
  const sunIcon = document.getElementById("theme-icon-sun");

  if (theme === "light") {
    if (moonIcon) moonIcon.style.display = "block";
    if (sunIcon) sunIcon.style.display = "none";
    if (toggleBtn) toggleBtn.setAttribute("aria-label", "Switch to dark mode");
  } else {
    if (moonIcon) moonIcon.style.display = "none";
    if (sunIcon) sunIcon.style.display = "block";
    if (toggleBtn) toggleBtn.setAttribute("aria-label", "Switch to light mode");
  }
}

function toggleTheme() {
  const newTheme = STATE.theme === "dark" ? "light" : "dark";
  setTheme(newTheme);
}

// ============================================================================
// 5. UI RENDERERS
// ============================================================================
function showToast(message, duration = 3000) {
  let toast = document.getElementById("alert-toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "alert-toast";
    toast.className = "app-toast";
    document.body.appendChild(toast);
  }
  toast.textContent = message;
  toast.classList.add("visible");
  setTimeout(() => toast.classList.remove("visible"), duration);
}

function updateBackendStatus(state, message) {
  const dot = document.getElementById("backend-status-dot");
  const text = document.getElementById("backend-status-text");
  if (!dot || !text) return;

  if (state === "connected") {
    dot.className = "status-dot healthy";
    text.textContent = message || "Connected";
  } else if (state === "connecting") {
    dot.className = "status-dot";
    text.textContent = message || "Connecting...";
  } else {
    dot.className = "status-dot error";
    text.textContent = message || "Backend unavailable";
  }
}

function renderAgentsList() {
  const grid = document.getElementById("agents-grid");
  const badge = document.getElementById("agents-count-badge");
  if (!grid) return;

  const count = STATE.agents.length;
  if (badge) badge.textContent = `${count} / ${STATE.maxAgents}`;

  if (count === 0) {
    grid.innerHTML = `
      <div class="empty-state" style="grid-column: 1 / -1; padding: 1.5rem;">
        No autonomous agents initialized yet. Create a persona below to begin.
      </div>
    `;
    return;
  }

  grid.innerHTML = STATE.agents.map(a => {
    const isSelected = (a.agentId === STATE.agentId);
    const status = a.status || {};
    const leader = status.currentLeader;
    const leaderScore = leader && leader.score ? Number(leader.score).toFixed(1) : "None";
    const candidateCount = status.candidateCount || 0;
    const windowStatus = status.windowStatus || "OPEN";

    return `
      <div class="agent-card ${isSelected ? 'active-selected' : ''}" data-agent-id="${escapeHtml(a.agentId)}">
        <div>
          <div class="agent-card-header">
            <div class="agent-card-title">${escapeHtml(a.name)}</div>
            <span class="badge ${isSelected ? 'active' : 'running'}">${isSelected ? 'Active' : 'Running'}</span>
          </div>
          <div class="agent-card-domain">${escapeHtml(a.domain)}</div>
          <div class="agent-card-metrics">
            <div class="metric-item">
              <span>Leader Score:</span>
              <strong>${leaderScore !== "None" ? leaderScore + " / 100" : "None (<75)"}</strong>
            </div>
            <div class="metric-item">
              <span>Candidates:</span>
              <strong>${candidateCount} stories</strong>
            </div>
            <div class="metric-item">
              <span>Window:</span>
              <strong>${escapeHtml(windowStatus)}</strong>
            </div>
          </div>
        </div>
        <div class="agent-card-action">
          <button type="button" class="btn-view-agent" onclick="selectAgent('${escapeHtml(a.agentId)}')">
            ${isSelected ? 'Inspecting' : 'View Agent'}
          </button>
        </div>
      </div>
    `;
  }).join("");
}

function selectAgent(agentId) {
  if (!agentId || agentId === STATE.agentId) return;

  const target = STATE.agents.find(a => a.agentId === agentId);
  if (!target) return;

  STATE.agentId = target.agentId;
  STATE.personaName = target.name;
  STATE.personaDomain = target.domain;

  try {
    localStorage.setItem("echomind_agent_id", target.agentId);
    localStorage.setItem("echomind_persona_name", target.name);
    localStorage.setItem("echomind_persona_domain", target.domain);
  } catch (e) {
    console.warn("Storage save:", e);
  }

  updateActiveSessionUI();
  renderAgentsList();
  refreshData();
}
window.selectAgent = selectAgent;

function renderStatus() {
  const container = document.getElementById("status-container");
  if (!container || !STATE.statusData) return;

  const data = STATE.statusData;
  const windowId = data.window ? data.window.windowId : "win-none";
  const windowStatus = data.window ? data.window.status : "OPEN";
  const candidateCount = data.window ? data.window.candidateCount : 0;
  const endsAt = data.window && data.window.endsAt ? data.window.endsAt : null;
  const leader = data.currentLeader;
  const lastPublishedAt = data.lastPublishedAt;

  let leaderHtml = `
    <div class="leader-box">
      <div class="leader-header">
        <span class="leader-badge">Window Leader</span>
        <span class="leader-score" style="font-size: 0.9rem; color: var(--text-muted);">None (< 75.0)</span>
      </div>
      <div class="leader-desc">No qualified candidate yet. Discovery loop evaluates every 5 minutes.</div>
    </div>
  `;

  if (leader) {
    leaderHtml = `
      <div class="leader-box active">
        <div class="leader-header">
          <span class="leader-badge">Top Candidate</span>
          <span class="leader-score">${Number(leader.score).toFixed(1)} / 100</span>
        </div>
        <div class="leader-title">${escapeHtml(leader.title)}</div>
        <div class="leader-desc">${escapeHtml(leader.summary || "")}</div>
      </div>
    `;
  }

  container.innerHTML = `
    <div class="card">
      <div class="card-title">
        <span>Publishing Window</span>
        <span class="badge ${windowStatus === 'OPEN' ? 'open' : 'published'}">${escapeHtml(windowStatus)}</span>
      </div>
      <div class="window-grid">
        <div class="stat-card">
          <span class="stat-label">Window ID</span>
          <span class="stat-value">${escapeHtml(windowId)}</span>
        </div>
        <div class="stat-card">
          <span class="stat-label">Evaluated Stories</span>
          <span class="stat-value">${candidateCount}</span>
        </div>
        <div class="stat-card">
          <span class="stat-label">Window Closes</span>
          <span class="stat-value">${formatDate(endsAt)}</span>
        </div>
        <div class="stat-card">
          <span class="stat-label">Last Published</span>
          <span class="stat-value">${formatDate(lastPublishedAt)}</span>
        </div>
      </div>
      ${leaderHtml}
    </div>
  `;
}

function renderFeed() {
  const container = document.getElementById("feed-container");
  const countEl = document.getElementById("feed-count");
  if (!container) return;

  if (!STATE.posts || STATE.posts.length === 0) {
    if (countEl) countEl.textContent = "(0)";
    container.innerHTML = `
      <div class="empty-state" id="feed-empty-state">
        No stories published to feed yet. At window close (${STATE.publishWindowMinutes || 120} min), the highest-scoring verified leader meeting the minimum threshold (75.0) will be published.
      </div>
    `;
    return;
  }

  if (countEl) countEl.textContent = `(${STATE.posts.length})`;

  container.innerHTML = STATE.posts.map(post => `
    <div class="feed-item">
      <div class="feed-meta">
        <span class="feed-id">${escapeHtml(post.id)}</span>
        <span>${formatDate(post.createdAt)}</span>
      </div>
      <div class="feed-text">${escapeHtml(post.text)}</div>
      <div class="feed-rationale">
        <strong>Editorial Rationale:</strong> ${escapeHtml(post.rationale)}
      </div>
      ${post.sources && post.sources.length ? `
        <div class="feed-sources">
          ${post.sources.map(s => `<a href="${escapeHtml(s)}" target="_blank" rel="noopener" class="source-link">${escapeHtml(formatSourceUrl(s))}</a>`).join("")}
        </div>
      ` : ""}
    </div>
  `).join("");
}

function formatSourceUrl(url) {
  try {
    const parsed = new URL(url);
    return parsed.hostname.replace("www.", "");
  } catch {
    return url;
  }
}

// Centralized IST Timezone Formatters (Asia/Kolkata / UTC+05:30)
const IST_TIME_FORMATTER = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false
});

const IST_DATETIME_FORMATTER = new Intl.DateTimeFormat("en-US", {
  timeZone: "Asia/Kolkata",
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: true
});

function formatISTTime(isoString) {
  if (!isoString) return "None";
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return isoString;
    return `${IST_TIME_FORMATTER.format(d)} IST`;
  } catch {
    return isoString;
  }
}

function formatISTDateTime(isoString) {
  if (!isoString) return "None";
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return isoString;
    return `${IST_DATETIME_FORMATTER.format(d)} IST`;
  } catch {
    return isoString;
  }
}

function formatDate(isoString) {
  return formatISTTime(isoString);
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function updateActiveSessionUI() {
  const sessionInfo = document.getElementById("session-info");
  const agentIdEl = document.getElementById("active-agent-id");
  const personaEl = document.getElementById("active-persona-title");

  if (STATE.agentId) {
    if (sessionInfo) sessionInfo.style.display = "block";
    if (agentIdEl) agentIdEl.textContent = STATE.agentId;
    if (personaEl) personaEl.textContent = `${STATE.personaName || "Persona"} (${STATE.personaDomain || "General Tech"})`;
  } else {
    if (sessionInfo) sessionInfo.style.display = "none";
  }
}

// ============================================================================
// 6. POLLING & INITIALIZATION WORKFLOWS
// ============================================================================
async function refreshData() {
  try {
    // 1. Fetch all agents
    const agentsRes = await apiClient.getAgents(STATE.agentId).catch(() => null);
    if (agentsRes && Array.isArray(agentsRes.agents)) {
      STATE.backendHealthy = true;
      updateBackendStatus("connected", "Connected");
      STATE.agents = agentsRes.agents;
      STATE.maxAgents = agentsRes.maxAgents || 5;
      if (agentsRes.publishWindowMinutes) {
        updateWindowCadenceUI(agentsRes.publishWindowMinutes);
      }
      renderAgentsList();

      // If active agent is missing or was rotated out, select the first available agent
      if (STATE.agentId && !STATE.agents.some(a => a.agentId === STATE.agentId)) {
        if (STATE.agents.length > 0) {
          const newest = STATE.agents[STATE.agents.length - 1];
          STATE.agentId = newest.agentId;
          STATE.personaName = newest.name;
          STATE.personaDomain = newest.domain;
          try {
            localStorage.setItem("echomind_agent_id", newest.agentId);
            localStorage.setItem("echomind_persona_name", newest.name);
            localStorage.setItem("echomind_persona_domain", newest.domain);
          } catch (e) {}
        } else {
          STATE.agentId = "";
          try {
            localStorage.removeItem("echomind_agent_id");
          } catch (e) {}
        }
        updateActiveSessionUI();
      }
    }

    if (!STATE.agentId && STATE.agents.length > 0) {
      const first = STATE.agents[0];
      STATE.agentId = first.agentId;
      STATE.personaName = first.name;
      STATE.personaDomain = first.domain;
      try {
        localStorage.setItem("echomind_agent_id", first.agentId);
        localStorage.setItem("echomind_persona_name", first.name);
        localStorage.setItem("echomind_persona_domain", first.domain);
      } catch (e) {}
      updateActiveSessionUI();
    }

    if (STATE.agentId) {
      const [statusData, feedData] = await Promise.all([
        apiClient.getStatus(STATE.agentId).catch(() => null),
        apiClient.getFeed(STATE.agentId).catch(() => ({ posts: [] }))
      ]);

      if (statusData) {
        STATE.statusData = statusData;
        renderStatus();
      }

      if (feedData && Array.isArray(feedData.posts)) {
        STATE.posts = feedData.posts;
        renderFeed();
      }
    }
  } catch (e) {
    console.error("[EchoMind Client] Refresh error:", e);
  }
}

async function handleInitForm(e) {
  e.preventDefault();
  const nameInput = document.getElementById("persona-name");
  const domainInput = document.getElementById("persona-domain");

  const name = nameInput.value.trim();
  const domain = domainInput.value.trim();

  if (!name || !domain) {
    showToast("Please enter both persona name and technical domain.");
    return;
  }

  // Check 5-agent limit confirmation modal
  if (STATE.agents.length >= 5) {
    const oldest = STATE.agents[0];
    const oldestName = oldest ? oldest.name : "the oldest agent";
    showCapacityModal(name, domain, oldestName);
    return;
  }

  await executeAgentCreation(name, domain);
}

function showCapacityModal(name, domain, oldestName) {
  STATE.pendingInitPayload = { name, domain };
  const modal = document.getElementById("delete-modal");
  const modalBody = document.getElementById("modal-body");
  if (modalBody) {
    modalBody.textContent = `You already have 5 autonomous agents. Creating ${name} will permanently remove the oldest agent, ${oldestName}.`;
  }
  if (modal) modal.classList.add("open");
}

function hideCapacityModal() {
  STATE.pendingInitPayload = null;
  const modal = document.getElementById("delete-modal");
  if (modal) modal.classList.remove("open");
}

async function executeAgentCreation(name, domain) {
  const submitBtn = document.getElementById("init-submit-btn");
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.innerHTML = `<span class="loading-spinner"></span> Initializing...`;
  }

  try {
    const res = await apiClient.initAgent(name, domain);
    STATE.agentId = res.agentId;
    STATE.personaName = name;
    STATE.personaDomain = domain;
    STATE.backendHealthy = true;
    updateBackendStatus("connected", "Connected");

    try {
      localStorage.setItem("echomind_agent_id", res.agentId);
      localStorage.setItem("echomind_persona_name", name);
      localStorage.setItem("echomind_persona_domain", domain);
    } catch (e) {}

    showToast(`Autonomous Persona '${name}' initialized: ${res.agentId}`);
    updateActiveSessionUI();
    await refreshData();
  } catch (err) {
    showToast(err.message || "Failed to initialize agent.");
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = "Initialize Autonomous Persona";
    }
    hideCapacityModal();
  }
}

// ============================================================================
// 7. STARTUP & LIFECYCLE
// ============================================================================
document.addEventListener("DOMContentLoaded", async () => {
  // 1. Initialize Theme
  try {
    initTheme();
  } catch (e) {
    console.error("[Theme] Init error:", e);
  }

  // Theme toggle button click
  const themeToggleBtn = document.getElementById("theme-toggle-btn");
  if (themeToggleBtn) {
    themeToggleBtn.addEventListener("click", toggleTheme);
  }

  // 2. Set configured backend label
  const displayUrl = (API_BASE_URL || window.location.host || "echomind-ltwo.onrender.com")
    .replace(/^https?:\/\//, "")
    .replace(/\/$/, "");
  const backendLabel = document.getElementById("backend-url-label");
  if (backendLabel) {
    backendLabel.textContent = displayUrl;
  }

  // 3. Health check runner
  const runHealthCheck = async () => {
    try {
      const health = await apiClient.checkHealth();
      if (health && (health.status === "healthy" || health.status === "ok" || health.scheduler_running !== undefined)) {
        updateBackendStatus("connected", "Connected");
        STATE.backendHealthy = true;
        if (health.publish_window_minutes) {
          updateWindowCadenceUI(health.publish_window_minutes);
        }
        return true;
      } else {
        if (!STATE.backendHealthy) {
          updateBackendStatus("error", "Backend unavailable");
        }
        return false;
      }
    } catch (err) {
      console.warn("[EchoMind] Health check warning:", err);
      if (!STATE.backendHealthy) {
        updateBackendStatus("error", "Backend unavailable");
      }
      return false;
    }
  };

  // Immediate health check
  updateBackendStatus("connecting", "Connecting...");
  await runHealthCheck();

  // 4. Modal event listeners
  const cancelBtn = document.getElementById("modal-cancel-btn");
  if (cancelBtn) {
    cancelBtn.addEventListener("click", hideCapacityModal);
  }

  const confirmBtn = document.getElementById("modal-confirm-btn");
  if (confirmBtn) {
    confirmBtn.addEventListener("click", async () => {
      if (STATE.pendingInitPayload) {
        await executeAgentCreation(STATE.pendingInitPayload.name, STATE.pendingInitPayload.domain);
      }
    });
  }

  // 5. Form listener
  const form = document.getElementById("persona-init-form");
  if (form) {
    form.addEventListener("submit", handleInitForm);
  }

  // 6. Restore session & load agents
  if (STATE.agentId) {
    updateActiveSessionUI();
  }
  await refreshData();

  // 7. Periodic polling (15s for data, 30s for healthz)
  setInterval(refreshData, 15000);
  setInterval(runHealthCheck, 30000);
});
