import React, { useEffect, useMemo, useRef, useSyncExternalStore } from "https://esm.sh/react@18.3.1";
import { createRoot } from "https://esm.sh/react-dom@18.3.1/client";
import htm from "https://esm.sh/htm@3.1.1";

const html = htm.bind(React.createElement);

const initialState = {
  connected: false,
  generatedAt: null,
  summary: { live_sources: 0, stale_sources: 0, covered_sources: 0, pending_reviews: 0, active_alerts: 0 },
  agents: [],
  pipeline: [],
  feedItems: [],
  reviewQueue: [],
  wsUrl: "",
  approvalEndpoint: "",
};

const dashboardState = { ...initialState };
const listeners = new Set();

function emit() {
  for (const listener of listeners) listener();
}

function patchState(nextPatch) {
  Object.assign(dashboardState, nextPatch);
  emit();
}

function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot() {
  return dashboardState;
}

function readBootstrap() {
  const script = document.getElementById("agent-dashboard-data");
  if (!script) return {};
  try {
    return JSON.parse(script.textContent || "{}");
  } catch {
    return {};
  }
}

function requestJson(url, options = {}) {
  return fetch(url, {
    method: options.method || "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    body: options.body ? JSON.stringify(options.body) : undefined,
  }).then(async (response) => {
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || "request_failed");
    }
    return data.data || data;
  });
}

function statusTone(status) {
  if (status === "success") return "text-emerald-400 border-emerald-500/20 bg-emerald-500/10";
  if (status === "error") return "text-rose-400 border-rose-500/20 bg-rose-500/10";
  return "text-amber-300 border-amber-500/20 bg-amber-500/10";
}

function statusDot(status) {
  if (status === "success") return "bg-emerald-400";
  if (status === "error") return "bg-rose-400";
  return "bg-amber-400";
}

function formatTime(value) {
  if (!value) return "—";
  try {
    return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return value;
  }
}

function formatTimestamp(value) {
  if (!value) return "Unknown";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

function formatUptime(seconds) {
  if (typeof seconds !== "number") return "Unknown";
  const total = Math.max(0, Math.floor(seconds));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function useAgentEvents() {
  const snapshot = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  useEffect(() => {
    const bootstrap = readBootstrap();
    patchState({
      ...bootstrap,
      connected: false,
      wsUrl: document.getElementById("agent-dashboard-root")?.dataset.wsUrl || bootstrap.ws_url || "",
      approvalEndpoint: document.getElementById("agent-dashboard-root")?.dataset.approvalEndpoint || bootstrap.approval_endpoint || "",
    });

    const wsUrl = document.getElementById("agent-dashboard-root")?.dataset.wsUrl || bootstrap.ws_url || "";
    if (!wsUrl) return () => {};

    let socket;
    try {
      const url = wsUrl.startsWith("ws") ? wsUrl : `${window.location.origin.replace(/^http/, "ws")}${wsUrl}`;
      socket = new WebSocket(url);
      socket.onopen = () => patchState({ connected: true });
      socket.onclose = () => patchState({ connected: false });
      socket.onerror = () => patchState({ connected: false });
      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (payload?.type === "snapshot") {
            patchState({ ...payload, connected: true });
            return;
          }
          if (payload?.agent_name) {
            patchState({
              feedItems: [payload, ...(dashboardState.feedItems || [])].slice(0, 100),
              agents: (dashboardState.agents || []).map((agent) => (
                agent.agent_id === payload.agent_id
                  ? { ...agent, status: payload.status || agent.status, last_sync_at: payload.created_at || agent.last_sync_at }
                  : agent
              )),
            });
          }
        } catch {
          // ignore malformed telemetry
        }
      };
    } catch {
      patchState({ connected: false });
    }

    return () => {
      if (socket) socket.close();
    };
  }, []);

  async function approveReview(item, approved) {
    const endpoint = dashboardState.approvalEndpoint || document.getElementById("agent-dashboard-root")?.dataset.approvalEndpoint || "";
    if (!endpoint) {
      patchState({
        reviewQueue: dashboardState.reviewQueue.map((entry) => (
          entry.id === item.id ? { ...entry, audit_status: approved ? "clean" : "pending", review_state: approved ? "approved" : "held" } : entry
        )),
      });
      return;
    }

    const nextPayload = await requestJson(`${endpoint}/${item.id}`, {
      body: { approved },
    });

    if (nextPayload && nextPayload.review_queue) {
      patchState({
        ...nextPayload,
        connected: dashboardState.connected,
      });
    } else if (nextPayload && nextPayload.review) {
      patchState({
        reviewQueue: dashboardState.reviewQueue.map((entry) => (
          entry.id === item.id
            ? { ...entry, audit_status: nextPayload.review.audit_status, review_state: nextPayload.review.approved ? "approved" : "held" }
            : entry
        )),
      });
    }
  }

  return { ...snapshot, approveReview };
}

function SectionShell({ eyebrow, title, subtitle, children, className = "" }) {
  return html`
    <section className=${`rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm ${className}`}>
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-[10px] font-black uppercase tracking-[0.22em] text-slate-400">${eyebrow}</p>
          <h2 className="mt-2 text-2xl font-black tracking-tight text-slate-950">${title}</h2>
          ${subtitle ? html`<p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">${subtitle}</p>` : null}
        </div>
      </div>
      <div className="mt-5">${children}</div>
    </section>
  `;
}

function HealthCard({ agent }) {
  return html`
    <article className="rounded-[24px] border border-slate-200 bg-slate-50 p-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-sm font-black text-slate-950">${agent.agent_name}</p>
          <p className="mt-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">${agent.description}</p>
        </div>
        <span className=${`inline-flex items-center rounded-full border px-2.5 py-1 text-[10px] font-black uppercase tracking-[0.16em] ${statusTone(agent.status)}`}>
          <span className=${`mr-2 h-2 w-2 rounded-full ${statusDot(agent.status)}`}></span>
          ${agent.status}
        </span>
      </div>

      <div className="mt-4 grid grid-cols-3 gap-3 text-xs">
        <div className="rounded-2xl bg-white p-3">
          <p className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-400">Uptime</p>
          <p className="mt-1 font-mono text-slate-900">${formatUptime(agent.uptime_seconds)}</p>
        </div>
        <div className="rounded-2xl bg-white p-3">
          <p className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-400">Last Sync</p>
          <p className="mt-1 font-mono text-slate-900">${formatTime(agent.last_sync_at)}</p>
        </div>
        <div className="rounded-2xl bg-white p-3">
          <p className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-400">Active Alerts</p>
          <p className="mt-1 font-mono text-slate-900">${agent.active_alerts ?? 0}</p>
        </div>
      </div>
    </article>
  `;
}

function PipelineStage({ stage }) {
  return html`
    <div className="flex items-start gap-4 rounded-[24px] border border-slate-200 bg-slate-50 p-4">
      <div className=${`mt-1 h-3.5 w-3.5 rounded-full ${statusDot(stage.status)}`}></div>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm font-black text-slate-950">${stage.label}</p>
          <span className=${`rounded-full border px-2.5 py-1 text-[10px] font-black uppercase tracking-[0.16em] ${statusTone(stage.status)}`}>${stage.status}</span>
        </div>
        <p className="mt-1 text-xs text-slate-500">${stage.detail}</p>
      </div>
    </div>
  `;
}

function LiveFeed({ items, connected }) {
  const feedRef = useRef(null);

  useEffect(() => {
    if (feedRef.current) {
      feedRef.current.scrollTop = feedRef.current.scrollHeight;
    }
  }, [items]);

  return html`
    <div className="rounded-[28px] border border-slate-800 bg-slate-950 text-slate-100 shadow-xl">
      <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
        <div>
          <p className="text-[10px] font-black uppercase tracking-[0.24em] text-cyan-300">Live Thought Stream</p>
          <p className="mt-1 text-xs text-slate-500">Terminal-style real-time agent logs</p>
        </div>
        <div className="flex items-center gap-2">
          <span className=${`h-2.5 w-2.5 rounded-full ${connected ? 'bg-emerald-400' : 'bg-slate-600'}`}></span>
          <span className="font-mono text-[11px] uppercase tracking-[0.2em] text-slate-400">${connected ? 'Connected' : 'Offline'}</span>
        </div>
      </div>
      <div ref=${feedRef} className="max-h-[28rem] overflow-y-auto px-4 py-3 font-mono text-sm">
        ${items.length
          ? items.map((item, index) => html`
              <div key=${`${item.id || index}`} className="mb-2 rounded-2xl border border-slate-800 bg-slate-900/70 px-3 py-2">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <span className="text-slate-300">${item.agent_name || 'Hermes'}</span>
                    <span className="mx-2 text-slate-600">·</span>
                    <span className="text-slate-500">${item.stage || 'scrape'}</span>
                    <span className="mx-2 text-slate-600">·</span>
                    <span className=${item.status === 'success' ? 'text-emerald-400' : item.status === 'error' ? 'text-rose-400' : 'text-amber-400'}>
                      ${(item.status || 'processing').toUpperCase()}
                    </span>
                  </div>
                  <time className="text-[11px] text-slate-600">${formatTime(item.created_at)}</time>
                </div>
                <p className="mt-1 text-slate-200">${item.message}</p>
              </div>
            `)
          : html`<div className="text-slate-500">Waiting for live telemetry…</div>`}
      </div>
    </div>
  `;
}

function ReviewQueue({ items, onDecision }) {
  return html`
    <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
      <div>
        <p className="text-[10px] font-black uppercase tracking-[0.22em] text-slate-400">Manual Intervention Toggle</p>
        <h3 className="mt-2 text-2xl font-black tracking-tight text-slate-950">Approve summaries before publication</h3>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">
          Toggle each draft between approved and held states. Approved summaries are eligible for the public site; held items stay internal.
        </p>
      </div>
      <div className="mt-5 space-y-3">
        ${items.length
          ? items.map((item) => html`
              <article key=${item.id} className="rounded-[22px] border border-slate-200 bg-slate-50 p-4">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="text-sm font-black text-slate-950">${item.title}</p>
                      <span className=${`rounded-full border px-2.5 py-1 text-[10px] font-black uppercase tracking-[0.16em] ${item.audit_status === 'clean' ? 'border-emerald-200 bg-emerald-100 text-emerald-700' : 'border-amber-200 bg-amber-100 text-amber-800'}`}>
                        ${item.audit_status}
                      </span>
                    </div>
                    <p className="mt-1 text-xs text-slate-500">${item.county || 'County pending'}${item.city ? ` · ${item.city}` : ''} · ${item.created_at_label}</p>
                    <p className="mt-2 line-clamp-2 text-sm text-slate-600">${item.summary || 'No summary provided.'}</p>
                    ${item.source_pdf_name
                      ? html`<p className="mt-2 text-[11px] font-mono text-slate-500">Source PDF: ${item.source_pdf_name}</p>`
                      : null}
                  </div>
                  <div className="flex flex-col gap-2 sm:flex-row">
                    <button
                      onClick=${() => onDecision(item, true)}
                      className="inline-flex items-center justify-center rounded-full bg-slate-950 px-4 py-2 text-[11px] font-black uppercase tracking-[0.18em] text-white transition hover:bg-slate-800"
                    >
                      Approve
                    </button>
                    <button
                      onClick=${() => onDecision(item, false)}
                      className="inline-flex items-center justify-center rounded-full border border-slate-300 bg-white px-4 py-2 text-[11px] font-black uppercase tracking-[0.18em] text-slate-700 transition hover:bg-slate-100"
                    >
                      Hold
                    </button>
                  </div>
                </div>
              </article>
            `)
          : html`<div className="rounded-[22px] border border-dashed border-slate-300 bg-slate-50 p-5 text-sm text-slate-500">No manual review items queued right now.</div>`}
      </div>
    </div>
  `;
}

function Dashboard() {
  const { connected, agents, pipeline, feedItems, reviewQueue, summary, approveReview } = useAgentEvents();
  const topStats = useMemo(() => ([
    { label: 'Live Sources', value: summary.live_sources },
    { label: 'Pending Reviews', value: summary.pending_reviews },
    { label: 'Stale Sources', value: summary.stale_sources },
    { label: 'Active Alerts', value: summary.active_alerts },
  ]), [summary]);

  return html`
    <div className="space-y-6">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        ${topStats.map((stat) => html`
          <div key=${stat.label} className="rounded-[24px] border border-slate-800 bg-slate-950 p-4 text-white shadow-lg">
            <p className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-400">${stat.label}</p>
            <p className="mt-2 text-3xl font-black">${stat.value}</p>
          </div>
        `)}
      </div>

      <div className="grid gap-6 lg:grid-cols-[1.25fr_0.75fr]">
        <${LiveFeed} items=${feedItems} connected=${connected} />

        <div className="space-y-6">
          <${SectionShell}
            eyebrow="System Health Cards"
            title="Agent status at a glance"
            subtitle="Minimalist uptime and sync cards keep the operator loop tight."
            children=${html`<div className="grid gap-3">${agents.map((agent) => html`<${HealthCard} key=${agent.agent_id} agent=${agent} />`)}</div>`}
          />

          <${SectionShell}
            eyebrow="Data Pipeline"
            title="Scrape → Analyze → Notify"
            subtitle="Each stage is visibly mapped to the current processing state."
            children=${html`<div className="space-y-3">${pipeline.map((stage) => html`<${PipelineStage} key=${stage.key} stage=${stage} />`)}</div>`}
          />
        </div>
      </div>

      <${ReviewQueue}
        items=${reviewQueue}
        onDecision=${approveReview}
      />
    </div>
  `;
}

const rootNode = document.getElementById('agent-dashboard-root');
if (rootNode) {
  createRoot(rootNode).render(html`<${Dashboard} />`);
}
