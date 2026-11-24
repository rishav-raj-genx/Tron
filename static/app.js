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
// 4. Default fallback: same-origin API
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
  return "";
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
      if (response.status === 429) {
        const retryAfter = response.headers.get("Retry-After") || "60";
        let detail = "Rate limit reached. Please wait before retrying.";
        try {
          const errJson = await response.json();
          if (errJson && errJson.detail) detail = errJson.detail;
        } catch (_) {}
        showToast(`Cooldown Active: ${detail} (Retry after ${retryAfter}s)`, 6000);
        const err = new Error(`Rate limit exceeded (429): Please wait ${retryAfter}s.`);
        err.status = 429;
        err.retryAfter = parseInt(retryAfter, 10) || 60;
        throw err;
      }
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

  grid.textContent = "";

  if (count === 0) {
    const emptyDiv = document.createElement("div");
    emptyDiv.className = "empty-state";
    emptyDiv.style.gridColumn = "1 / -1";
    emptyDiv.style.padding = "1.5rem";
    emptyDiv.textContent = "No autonomous agents initialized yet. Create a persona below to begin.";
    grid.appendChild(emptyDiv);
    return;
  }

  STATE.agents.forEach(a => {
    const isSelected = (a.agentId === STATE.agentId);
    const status = a.status || {};
    const leader = status.currentLeader;
    const leaderScore = leader && leader.score ? Number(leader.score).toFixed(1) : "None";
    const candidateCount = status.candidateCount || 0;
    const windowStatus = status.windowStatus || "OPEN";

    const card = document.createElement("div");
    card.className = `agent-card ${isSelected ? 'active-selected' : ''}`;
    card.setAttribute("data-agent-id", a.agentId);

    const topDiv = document.createElement("div");

    const header = document.createElement("div");
    header.className = "agent-card-header";

    const title = document.createElement("div");
    title.className = "agent-card-title";
    title.textContent = a.name;

    const statusBadge = document.createElement("span");
    statusBadge.className = `badge ${isSelected ? 'active' : 'running'}`;
    statusBadge.textContent = isSelected ? "Active" : "Running";

    header.appendChild(title);
    header.appendChild(statusBadge);

    const domain = document.createElement("div");
    domain.className = "agent-card-domain";
    domain.textContent = a.domain;

    const metrics = document.createElement("div");
    metrics.className = "agent-card-metrics";

    const metricLeader = document.createElement("div");
    metricLeader.className = "metric-item";
    const leaderLabel = document.createElement("span");
    leaderLabel.textContent = "Leader Score:";
    const leaderVal = document.createElement("strong");
    leaderVal.textContent = leaderScore !== "None" ? `${leaderScore} / 100` : "None (<75)";
    metricLeader.appendChild(leaderLabel);
    metricLeader.appendChild(leaderVal);

    const metricCand = document.createElement("div");
    metricCand.className = "metric-item";
    const candLabel = document.createElement("span");
    candLabel.textContent = "Candidates:";
    const candVal = document.createElement("strong");
    candVal.textContent = `${candidateCount} stories`;
    metricCand.appendChild(candLabel);
    metricCand.appendChild(candVal);

    const metricWin = document.createElement("div");
    metricWin.className = "metric-item";
    const winLabel = document.createElement("span");
    winLabel.textContent = "Window:";
    const winVal = document.createElement("strong");
    winVal.textContent = windowStatus;
    metricWin.appendChild(winLabel);
    metricWin.appendChild(winVal);

    metrics.appendChild(metricLeader);
    metrics.appendChild(metricCand);
    metrics.appendChild(metricWin);

    topDiv.appendChild(header);
    topDiv.appendChild(domain);
    topDiv.appendChild(metrics);

    const actionDiv = document.createElement("div");
    actionDiv.className = "agent-card-action";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn-view-agent";
    btn.textContent = isSelected ? "Inspecting" : "View Agent";
    btn.addEventListener("click", () => selectAgent(a.agentId));
    actionDiv.appendChild(btn);

    card.appendChild(topDiv);
    card.appendChild(actionDiv);

    grid.appendChild(card);
  });
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

  container.textContent = "";

  const card = document.createElement("div");
  card.className = "card";

  const cardTitle = document.createElement("div");
  cardTitle.className = "card-title";
  const titleSpan = document.createElement("span");
  titleSpan.textContent = "Publishing Window";
  const badgeSpan = document.createElement("span");
  badgeSpan.className = `badge ${windowStatus === 'OPEN' ? 'open' : 'published'}`;
  badgeSpan.textContent = windowStatus;
  cardTitle.appendChild(titleSpan);
  cardTitle.appendChild(badgeSpan);
  card.appendChild(cardTitle);

  const windowGrid = document.createElement("div");
  windowGrid.className = "window-grid";

  const stats = [
    { label: "Window ID", val: windowId },
    { label: "Evaluated Stories", val: String(candidateCount) },
    { label: "Window Closes", val: formatDate(endsAt) },
    { label: "Last Published", val: formatDate(lastPublishedAt) },
  ];

  stats.forEach(st => {
    const statCard = document.createElement("div");
    statCard.className = "stat-card";
    const lbl = document.createElement("span");
    lbl.className = "stat-label";
    lbl.textContent = st.label;
    const val = document.createElement("span");
    val.className = "stat-value";
    val.textContent = st.val;
    statCard.appendChild(lbl);
    statCard.appendChild(val);
    windowGrid.appendChild(statCard);
  });
  card.appendChild(windowGrid);

  const leaderBox = document.createElement("div");
  if (leader) {
    leaderBox.className = "leader-box active";

    const leaderHeader = document.createElement("div");
    leaderHeader.className = "leader-header";
    const lBadge = document.createElement("span");
    lBadge.className = "leader-badge";
    lBadge.textContent = "Top Candidate";
    const lScore = document.createElement("span");
    lScore.className = "leader-score";
    lScore.textContent = `${Number(leader.score).toFixed(1)} / 100`;
    leaderHeader.appendChild(lBadge);
    leaderHeader.appendChild(lScore);

    const lTitle = document.createElement("div");
    lTitle.className = "leader-title";
    lTitle.textContent = leader.title || "";

    const lDesc = document.createElement("div");
    lDesc.className = "leader-desc";
    lDesc.textContent = leader.summary || "";

    leaderBox.appendChild(leaderHeader);
    leaderBox.appendChild(lTitle);
    leaderBox.appendChild(lDesc);
  } else {
    leaderBox.className = "leader-box";

    const leaderHeader = document.createElement("div");
    leaderHeader.className = "leader-header";
    const lBadge = document.createElement("span");
    lBadge.className = "leader-badge";
    lBadge.textContent = "Window Leader";
    const lScore = document.createElement("span");
    lScore.className = "leader-score";
    lScore.style.fontSize = "0.9rem";
    lScore.style.color = "var(--text-muted)";
    lScore.textContent = "None (< 75.0)";
    leaderHeader.appendChild(lBadge);
    leaderHeader.appendChild(lScore);

    const lDesc = document.createElement("div");
    lDesc.className = "leader-desc";
    lDesc.textContent = "No qualified candidate yet. Continuous discovery loops evaluate incoming stories.";

    leaderBox.appendChild(leaderHeader);
    leaderBox.appendChild(lDesc);
  }
  card.appendChild(leaderBox);

  container.appendChild(card);
}

function renderFeed() {
  const container = document.getElementById("feed-container");
  const countEl = document.getElementById("feed-count");
  if (!container) return;

  if (!STATE.posts || STATE.posts.length === 0) {
    if (countEl) countEl.textContent = "(0)";
    container.textContent = "";

    const emptyCard = document.createElement("div");
    emptyCard.className = "empty-state-card";
    emptyCard.id = "feed-empty-state";

    const emptyTitle = document.createElement("h4");
    emptyTitle.className = "empty-state-title";
    emptyTitle.textContent = "No Published Stories Yet";

    const emptyDesc = document.createElement("p");
    emptyDesc.className = "empty-state-desc";
    emptyDesc.textContent = `Tron enforces a strict 75.0/100 editorial quality bar across multi-factor scoring (recency, significance, domain relevance, source quality, novelty, and verifiability). At window close (${STATE.publishWindowMinutes || 120} min), the highest-scoring verified leader meeting the minimum threshold will be published.`;

    const qualityBar = document.createElement("span");
    qualityBar.className = "empty-state-quality-bar";
    qualityBar.textContent = "Editorial Quality Bar: >= 75.0 / 100";

    emptyCard.appendChild(emptyTitle);
    emptyCard.appendChild(emptyDesc);
    emptyCard.appendChild(qualityBar);
    container.appendChild(emptyCard);
    return;
  }

  if (countEl) countEl.textContent = `(${STATE.posts.length})`;
  container.textContent = "";

  STATE.posts.forEach(post => {
    const item = document.createElement("div");
    item.className = "feed-item";

    const meta = document.createElement("div");
    meta.className = "feed-meta";

    const idSpan = document.createElement("span");
    idSpan.className = "feed-id";
    idSpan.textContent = post.id || "";

    const dateSpan = document.createElement("span");
    dateSpan.textContent = formatDate(post.createdAt);

    meta.appendChild(idSpan);
    meta.appendChild(dateSpan);

    const textDiv = document.createElement("div");
    textDiv.className = "feed-text";
    textDiv.textContent = post.text || "";

    const ratDiv = document.createElement("div");
    ratDiv.className = "feed-rationale";
    const ratStrong = document.createElement("strong");
    ratStrong.textContent = "Editorial Rationale: ";
    const ratText = document.createTextNode(post.rationale || "");
    ratDiv.appendChild(ratStrong);
    ratDiv.appendChild(ratText);

    item.appendChild(meta);
    item.appendChild(textDiv);
    item.appendChild(ratDiv);

    if (Array.isArray(post.sources) && post.sources.length > 0) {
      const sourcesDiv = document.createElement("div");
      sourcesDiv.className = "feed-sources";

      post.sources.forEach(src => {
        if (typeof src === "string" && (src.startsWith("https://") || src.startsWith("http://"))) {
          const a = document.createElement("a");
          a.href = src;
          a.target = "_blank";
          a.rel = "noopener noreferrer";
          a.className = "source-link";
          a.textContent = formatSourceUrl(src);
          sourcesDiv.appendChild(a);
        }
      });

      if (sourcesDiv.childNodes.length > 0) {
        item.appendChild(sourcesDiv);
      }
    }

    container.appendChild(item);
  });
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
    submitBtn.textContent = "";
    const spinner = document.createElement("span");
    spinner.className = "loading-spinner";
    submitBtn.appendChild(spinner);
    submitBtn.appendChild(document.createTextNode(" Initializing..."));
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
    if (err.status === 429 && submitBtn) {
      let secondsLeft = err.retryAfter || 60;
      submitBtn.disabled = true;
      submitBtn.textContent = `Rate Limited (Wait ${secondsLeft}s)`;
      const interval = setInterval(() => {
        secondsLeft -= 1;
        if (secondsLeft <= 0) {
          clearInterval(interval);
          submitBtn.disabled = false;
          submitBtn.textContent = "Initialize Autonomous Persona";
        } else {
          submitBtn.textContent = `Rate Limited (Wait ${secondsLeft}s)`;
        }
      }, 1000);
      return;
    }
    showToast(err.message || "Failed to initialize agent.");
  } finally {
    if (submitBtn && !submitBtn.textContent.startsWith("Rate Limited")) {
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
  const displayUrl = (API_BASE_URL || window.location.host || "same-origin")
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

  // 5. Form listener & Agent selection delegation
  const form = document.getElementById("persona-init-form");
  if (form) {
    form.addEventListener("submit", handleInitForm);
  }

  const agentsGrid = document.getElementById("agents-grid");
  if (agentsGrid) {
    agentsGrid.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-select-agent]");
      if (btn) {
        const aid = btn.getAttribute("data-select-agent");
        if (aid) selectAgent(aid);
      }
    });
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
