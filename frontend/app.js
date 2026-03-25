const AGENTS = {
  Sarah: { color: "#D9480F", avatar: "SA" },
  Kai: { color: "#1D4ED8", avatar: "KA" },
  Tom: { color: "#6B7280", avatar: "TO" },
  Lara: { color: "#15803D", avatar: "LA" },
  Jonas: { color: "#7C3AED", avatar: "JO" },
  Andreas: { color: "#B45309", avatar: "AN" },
  Nina: { color: "#BE185D", avatar: "NI" },
  Facilitator: { color: "#0F766E", avatar: "FC" },
  System: { color: "#334155", avatar: "SY" },
  Operator: { color: "#0EA5E9", avatar: "OP" },
};
const SPEAKER_SEQUENCE = ["Sarah", "Kai", "Tom", "Lara", "Jonas", "Andreas", "Nina", "Facilitator"];

let sessionId = null;
let eventSource = null;
let activeThinking = new Set();

const els = {
  repoPath: document.getElementById("repoPath"),
  startSessionBtn: document.getElementById("startSessionBtn"),
  sessionId: document.getElementById("sessionId"),
  stackSummary: document.getElementById("stackSummary"),
  sourceSummary: document.getElementById("sourceSummary"),
  costCounter: document.getElementById("costCounter"),
  tokenCounter: document.getElementById("tokenCounter"),
  messages: document.getElementById("messages"),
  sendBtn: document.getElementById("sendBtn"),
  messageInput: document.getElementById("messageInput"),
  connectionStatus: document.getElementById("connectionStatus"),
  thinkingStatus: document.getElementById("thinkingStatus"),
  processingIndicator: document.getElementById("processingIndicator"),
  currentSpeaker: document.getElementById("currentSpeaker"),
  nextSpeaker: document.getElementById("nextSpeaker"),
  agentLegend: document.getElementById("agentLegend"),
};

function initLegend() {
  const names = ["Sarah", "Kai", "Tom", "Lara", "Jonas", "Andreas", "Nina"];
  for (const name of names) {
    const li = document.createElement("li");
    const badge = avatarNode(name);
    const text = document.createElement("span");
    text.textContent = name;
    li.appendChild(badge);
    li.appendChild(text);
    els.agentLegend.appendChild(li);
  }
}

function avatarNode(name) {
  const data = AGENTS[name] || { color: "#475569", avatar: "??" };
  const span = document.createElement("span");
  span.className = "avatar";
  span.style.backgroundColor = data.color;
  span.textContent = data.avatar;
  return span;
}

function setConnection(online) {
  els.connectionStatus.textContent = online ? "Connected" : "Offline";
  els.connectionStatus.classList.toggle("online", online);
  els.connectionStatus.classList.toggle("offline", !online);
}

function setSessionActive(active) {
  els.startSessionBtn.textContent = active ? "Stop Session" : "Start Session";
  els.sendBtn.disabled = !active;
  updateProcessingIndicator();
}

function currentThinkingAgent() {
  for (const speaker of SPEAKER_SEQUENCE) {
    if (activeThinking.has(speaker)) {
      return speaker;
    }
  }
  for (const speaker of activeThinking) {
    return speaker;
  }
  return null;
}

function nextSpeakerAfter(agentName) {
  const idx = SPEAKER_SEQUENCE.indexOf(agentName);
  if (idx === -1 || idx + 1 >= SPEAKER_SEQUENCE.length) {
    return null;
  }
  return SPEAKER_SEQUENCE[idx + 1];
}

function updateProcessingIndicator() {
  if (!els.processingIndicator || !els.currentSpeaker || !els.nextSpeaker) {
    return;
  }

  const activeAgent = currentThinkingAgent();
  if (activeAgent) {
    const next = nextSpeakerAfter(activeAgent);
    const color = (AGENTS[activeAgent] || {}).color || "#475569";
    els.processingIndicator.style.setProperty("--processing-color", color);
    els.processingIndicator.classList.add("active");
    els.processingIndicator.classList.remove("idle");
    els.currentSpeaker.textContent = `${activeAgent} is processing`;
    els.nextSpeaker.textContent = next ? `Up next: ${next}` : "Up next: waiting for facilitator wrap-up";
    return;
  }

  els.processingIndicator.style.setProperty("--processing-color", "#64748b");
  els.processingIndicator.classList.add("idle");
  els.processingIndicator.classList.remove("active");
  if (sessionId) {
    els.currentSpeaker.textContent = "Waiting for the next agent turn";
    els.nextSpeaker.textContent = "Upcoming speaker appears while processing";
  } else {
    els.currentSpeaker.textContent = "No active turn";
    els.nextSpeaker.textContent = "Start a session to begin";
  }
}

function updateThinkingLabel() {
  const activeAgent = currentThinkingAgent();
  if (!activeAgent) {
    els.thinkingStatus.textContent = "Idle";
    updateProcessingIndicator();
    return;
  }
  const next = nextSpeakerAfter(activeAgent);
  els.thinkingStatus.textContent = next ? `Thinking: ${activeAgent} (next: ${next})` : `Thinking: ${activeAgent}`;
  updateProcessingIndicator();
}

function appendMessage(message) {
  const wrapper = document.createElement("article");
  wrapper.className = `msg ${message.role}`;

  const meta = document.createElement("div");
  meta.className = "meta";
  meta.appendChild(avatarNode(message.author));
  const author = document.createElement("strong");
  author.textContent = message.author;
  meta.appendChild(author);

  const content = document.createElement("div");
  content.className = "content";
  content.textContent = message.content;

  wrapper.appendChild(meta);
  wrapper.appendChild(content);

  if (Array.isArray(message.references) && message.references.length > 0) {
    const refs = document.createElement("div");
    refs.className = "refs";
    refs.textContent = `References: ${message.references.join(" | ")}`;
    wrapper.appendChild(refs);
  }

  els.messages.appendChild(wrapper);
  els.messages.scrollTop = els.messages.scrollHeight;
}

function connectStream(id) {
  if (eventSource) {
    eventSource.close();
  }

  eventSource = new EventSource(`/api/stream/${id}`);

  eventSource.addEventListener("open", () => setConnection(true));
  eventSource.onerror = () => setConnection(false);

  eventSource.addEventListener("chat_message", (event) => {
    const payload = JSON.parse(event.data);
    appendMessage(payload.message);
  });

  eventSource.addEventListener("thinking", (event) => {
    const payload = JSON.parse(event.data);
    if (payload.state === "start") {
      activeThinking.add(payload.agent);
    } else {
      activeThinking.delete(payload.agent);
    }
    updateThinkingLabel();
  });

  eventSource.addEventListener("cost_update", (event) => {
    const payload = JSON.parse(event.data);
    els.costCounter.textContent = `$${Number(payload.total_cost_usd || 0).toFixed(6)}`;
    const totalTokens = Number(payload.total_input_tokens || 0) + Number(payload.total_output_tokens || 0);
    els.tokenCounter.textContent = String(totalTokens);
  });

  eventSource.addEventListener("agent_error", (event) => {
    try {
      const payload = JSON.parse(event.data);
      appendMessage({
        role: "system",
        author: "System",
        content: `Error: ${payload.message}`,
        references: [],
      });
    } catch (_err) {
      appendMessage({
        role: "system",
        author: "System",
        content: "An error occurred while processing the stream.",
        references: [],
      });
    }
  });
}

function stopSession() {
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  sessionId = null;
  activeThinking.clear();
  updateThinkingLabel();
  setConnection(false);
  setSessionActive(false);
  els.sessionId.textContent = "-";
  els.stackSummary.textContent = "-";
  els.sourceSummary.textContent = "-";
  els.costCounter.textContent = "$0.000000";
  els.tokenCounter.textContent = "0";
}

async function loadSnapshot(id) {
  const resp = await fetch(`/api/session/${id}`);
  if (!resp.ok) {
    throw new Error("Failed to load session snapshot.");
  }
  const data = await resp.json();
  els.messages.innerHTML = "";
  for (const message of data.messages) {
    appendMessage(message);
  }
  const totalTokens = Number(data.total_input_tokens || 0) + Number(data.total_output_tokens || 0);
  els.tokenCounter.textContent = String(totalTokens);
  els.costCounter.textContent = `$${Number(data.total_cost_usd || 0).toFixed(6)}`;
}

async function startSession() {
  const repoPath = els.repoPath.value.trim();
  if (!repoPath) {
    alert("Please provide a repository path.");
    return;
  }

  els.startSessionBtn.disabled = true;
  try {
    const resp = await fetch("/api/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repo_path: repoPath }),
    });

    if (!resp.ok) {
      const error = await resp.json();
      throw new Error(error.detail || "Failed to create session.");
    }

    const data = await resp.json();
    sessionId = data.session_id;
    els.sessionId.textContent = sessionId;
    els.stackSummary.textContent = data.stack.join(", ") || "Unknown";
    els.sourceSummary.textContent = `${data.source_kind} (${data.cache_hit ? "cache hit" : "fresh"})`;
    setSessionActive(true);
    connectStream(sessionId);
    await loadSnapshot(sessionId);
  } catch (err) {
    alert(err.message);
  } finally {
    els.startSessionBtn.disabled = false;
  }
}

async function toggleSession() {
  if (sessionId) {
    stopSession();
    return;
  }
  await startSession();
}

async function sendMessage() {
  if (!sessionId) {
    alert("Start a session first.");
    return;
  }

  const content = els.messageInput.value.trim();
  if (!content) return;

  els.messageInput.value = "";

  try {
    const resp = await fetch("/api/message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, content }),
    });

    if (!resp.ok) {
      const error = await resp.json();
      throw new Error(error.detail || "Failed to send message.");
    }
  } catch (err) {
    appendMessage({
      role: "system",
      author: "System",
      content: `Failed to send message: ${err.message}`,
      references: [],
    });
  }
}

els.startSessionBtn.addEventListener("click", toggleSession);
els.sendBtn.addEventListener("click", sendMessage);
els.messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendMessage();
  }
});

initLegend();
setSessionActive(false);
setConnection(false);
updateThinkingLabel();
