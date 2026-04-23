import React, { useEffect, useMemo, useState } from "https://esm.sh/react@18.3.1";
import { createRoot } from "https://esm.sh/react-dom@18.3.1/client";
import htm from "https://esm.sh/htm@3.1.1";

const html = htm.bind(React.createElement);

function readBootstrap() {
  const script = document.getElementById("bondsman-command-center-data");
  if (!script) return { watchlists: [], alerts: [], leads: [], cases: [], locations: [], pollIntervalSeconds: 30 };
  try {
    return JSON.parse(script.textContent || "{}");
  } catch {
    return { watchlists: [], alerts: [], leads: [], cases: [], locations: [], pollIntervalSeconds: 30 };
  }
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": document.querySelector("#bondsman-command-center-root")?.dataset.csrfToken || "",
      ...(options.headers || {}),
    },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || "request_failed");
  }
  return data.data;
}

function StatCard({ label, value, accent }) {
  return html`
    <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
      <p className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-400">${label}</p>
      <p className=${`mt-3 text-3xl font-black ${accent}`}>${value}</p>
    </div>
  `;
}

function WatchlistForm({ onCreate }) {
  const [watchName, setWatchName] = useState("");
  const [dateOfBirth, setDateOfBirth] = useState("");
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setSubmitting(true);
    try {
      await onCreate({ watchName, dateOfBirth, notes });
      setWatchName("");
      setDateOfBirth("");
      setNotes("");
    } finally {
      setSubmitting(false);
    }
  }

  return html`
    <form onSubmit=${submit} className="rounded-[32px] border border-slate-200 bg-white p-6 shadow-sm">
      <div className="flex items-center justify-between gap-4">
        <div>
          <p className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-400">Defendant Watch</p>
          <h2 className="mt-2 text-2xl font-black text-slate-900">Add watchlist entry</h2>
        </div>
      </div>
      <div className="mt-6 grid gap-4 md:grid-cols-2">
        <label className="block">
          <span className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-500">Defendant Name</span>
          <input value=${watchName} onInput=${(e) => setWatchName(e.target.value)} className="mt-2 w-full rounded-2xl border border-slate-300 bg-slate-50 px-4 py-3 text-sm" required />
        </label>
        <label className="block">
          <span className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-500">Date of Birth</span>
          <input type="date" value=${dateOfBirth} onInput=${(e) => setDateOfBirth(e.target.value)} className="mt-2 w-full rounded-2xl border border-slate-300 bg-slate-50 px-4 py-3 text-sm" />
        </label>
      </div>
      <label className="mt-4 block">
        <span className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-500">Notes</span>
        <textarea value=${notes} onInput=${(e) => setNotes(e.target.value)} rows="3" className="mt-2 w-full rounded-2xl border border-slate-300 bg-slate-50 px-4 py-3 text-sm"></textarea>
      </label>
      <button disabled=${submitting} className="mt-5 rounded-full bg-slate-950 px-5 py-3 text-[11px] font-black uppercase tracking-[0.18em] text-white disabled:opacity-50">
        ${submitting ? "Saving..." : "Add Watch"}
      </button>
    </form>
  `;
}

function WatchlistTable({ items, onDelete }) {
  return html`
    <div className="rounded-[32px] border border-slate-200 bg-white p-6 shadow-sm">
      <div className="flex items-center justify-between gap-4">
        <div>
          <p className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-400">Watchlist</p>
          <h2 className="mt-2 text-2xl font-black text-slate-900">Tracked defendants</h2>
        </div>
      </div>
      <div className="mt-5 space-y-3">
        ${items.length
          ? items.map(
              (item) => html`
                <div key=${item.id} className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <p className="text-sm font-black text-slate-900">${item.watchName}</p>
                      <p className="mt-1 text-xs text-slate-500">${item.dateOfBirth || "DOB pending"}</p>
                      <p className="mt-2 text-xs text-slate-600">${item.notes || "No notes."}</p>
                    </div>
                    <button onClick=${() => onDelete(item.id)} className="rounded-full border border-rose-200 px-3 py-1 text-[10px] font-black uppercase tracking-[0.18em] text-rose-600">
                      Remove
                    </button>
                  </div>
                </div>
              `
            )
          : html`<p className="text-sm text-slate-500">No watchlist entries yet.</p>`}
      </div>
    </div>
  `;
}

function AlertsFeed({ alerts, onAcknowledge }) {
  return html`
    <div className="rounded-[32px] border border-amber-200 bg-amber-50 p-6 shadow-sm">
      <div className="flex items-center justify-between gap-4">
        <div>
          <p className="text-[10px] font-black uppercase tracking-[0.18em] text-amber-700">Live Alerts</p>
          <h2 className="mt-2 text-2xl font-black text-slate-900">Watchlist matches</h2>
        </div>
      </div>
      <div className="mt-5 space-y-3">
        ${alerts.length
          ? alerts.map(
              (alert) => html`
                <div key=${alert.id} className="rounded-2xl border border-amber-200 bg-white p-4">
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <p className="text-sm font-black text-slate-900">${alert.defendantName}</p>
                      <p className="mt-1 text-xs text-slate-500">${alert.countyName} • ${alert.createdAt}</p>
                      <p className="mt-2 text-xs text-slate-600">${alert.chargesSummary || "Charge detail pending."}</p>
                    </div>
                    ${alert.alertStatus === "open"
                      ? html`<button onClick=${() => onAcknowledge(alert.id)} className="rounded-full bg-amber-500 px-3 py-1 text-[10px] font-black uppercase tracking-[0.18em] text-slate-950">Acknowledge</button>`
                      : html`<span className="text-[10px] font-black uppercase tracking-[0.18em] text-emerald-600">Acknowledged</span>`}
                  </div>
                </div>
              `
            )
          : html`<p className="text-sm text-amber-800">No live alerts right now.</p>`}
      </div>
    </div>
  `;
}

function LeadsPanel({ leads, onClaim }) {
  return html`
    <div className="rounded-[32px] border border-slate-200 bg-white p-6 shadow-sm">
      <div>
        <p className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-400">Lead Generation</p>
        <h2 className="mt-2 text-2xl font-black text-slate-900">Recent bondable bookings</h2>
      </div>
      <div className="mt-5 space-y-3">
        ${leads.length
          ? leads.map(
              (lead) => html`
                <div key=${lead.bookingId} className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="text-sm font-black text-slate-900">${lead.defendantName}</p>
                        <span className=${`rounded-full px-2.5 py-1 text-[10px] font-black uppercase tracking-[0.16em] ${lead.bondableStatus === "likely_bondable" ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-700"}`}>${lead.bondableStatus === "likely_bondable" ? "Likely Bondable" : "Manual Review"}</span>
                      </div>
                      <p className="mt-1 text-xs text-slate-500">${lead.countyName} • ${lead.bookingAt || "Booking time pending"}</p>
                      <p className="mt-2 text-xs text-slate-600">${lead.chargesSummary || "Charge detail pending."}</p>
                    </div>
                    <button onClick=${() => onClaim(lead.bookingId)} className="rounded-full bg-slate-950 px-3 py-1 text-[10px] font-black uppercase tracking-[0.18em] text-white">Claim Lead</button>
                  </div>
                </div>
              `
            )
          : html`<p className="text-sm text-slate-500">No open leads matched the current filter.</p>`}
      </div>
    </div>
  `;
}

function Countdown({ courtDate, danger }) {
  const text = useMemo(() => {
    if (!courtDate) return "Court date not set";
    const target = new Date(courtDate);
    if (Number.isNaN(target.getTime())) return courtDate;
    const ms = target.getTime() - Date.now();
    const hours = Math.round(ms / 3600000);
    if (hours >= 24) return `${Math.round(hours / 24)} day${Math.round(hours / 24) === 1 ? "" : "s"} left`;
    return `${hours}h left`;
  }, [courtDate]);
  return html`<span className=${`rounded-full px-2.5 py-1 text-[10px] font-black uppercase tracking-[0.16em] ${danger ? "bg-rose-100 text-rose-700" : "bg-slate-100 text-slate-600"}`}>${text}</span>`;
}

function CasesPanel({ cases, onUpdate }) {
  return html`
    <div className="rounded-[32px] border border-slate-200 bg-white p-6 shadow-sm">
      <div>
        <p className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-400">Case Lifecycle</p>
        <h2 className="mt-2 text-2xl font-black text-slate-900">Active cases</h2>
      </div>
      <div className="mt-5 space-y-4">
        ${cases.length
          ? cases.map(
              (item) => html`
                <div key=${item.caseId} className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                  <div className="flex flex-wrap items-start justify-between gap-4">
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="text-sm font-black text-slate-900">${item.defendantName}</p>
                        <${Countdown} courtDate=${item.courtDate} danger=${item.isForfeitureRisk} />
                      </div>
                      <p className="mt-1 text-xs text-slate-500">Payment: ${item.paymentStatus.replaceAll("_", " ")} • Last check-in: ${item.lastCheckinAt || "none yet"}</p>
                      <p className="mt-2 text-xs text-slate-600">${item.notes || "No case notes yet."}</p>
                      <p className="mt-2 text-[11px] font-black text-slate-700">Public check-in link: <a className="text-blue-600 underline" href=${`/checkin/${item.checkinToken}`}>/checkin/${item.checkinToken}</a></p>
                    </div>
                    <button onClick=${() => onUpdate(item)} className="rounded-full border border-slate-300 px-3 py-1 text-[10px] font-black uppercase tracking-[0.18em] text-slate-700">Update Case</button>
                  </div>
                </div>
              `
            )
          : html`<p className="text-sm text-slate-500">No active cases claimed yet.</p>`}
      </div>
    </div>
  `;
}

function MapPanel({ locations }) {
  useEffect(() => {
    const container = document.getElementById("bondsman-map");
    if (!container || !window.L) return;
    container.innerHTML = "";
    const map = window.L.map(container).setView([47.0, -110.5], 5.5);
    window.L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap contributors",
    }).addTo(map);
    if (locations.length) {
      const markers = locations.map((loc) =>
        window.L.marker([loc.latitude, loc.longitude]).addTo(map).bindPopup(
          `<strong>${loc.defendantName}</strong><br>${loc.lastCheckinAt || "Check-in received"}`
        )
      );
      const group = window.L.featureGroup(markers);
      map.fitBounds(group.getBounds().pad(0.25));
    }
    return () => map.remove();
  }, [locations]);

  return html`
    <div className="rounded-[32px] border border-slate-200 bg-white p-6 shadow-sm">
      <div>
        <p className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-400">Client Check-Ins</p>
        <h2 className="mt-2 text-2xl font-black text-slate-900">Last known locations</h2>
      </div>
      <div id="bondsman-map" className="mt-5 h-[360px] overflow-hidden rounded-[24px] border border-slate-200"></div>
    </div>
  `;
}

function App() {
  const rootNode = document.getElementById("bondsman-command-center-root");
  const [state, setState] = useState(readBootstrap());
  const [error, setError] = useState("");

  async function refresh() {
    try {
      const data = await requestJson(rootNode.dataset.bootstrapUrl, { method: "GET", headers: {} });
      setState(data);
      setError("");
    } catch (err) {
      setError(err.message || "refresh_failed");
    }
  }

  useEffect(() => {
    const interval = window.setInterval(refresh, (state.pollIntervalSeconds || 30) * 1000);
    return () => window.clearInterval(interval);
  }, [state.pollIntervalSeconds]);

  async function createWatchlist(payload) {
    const data = await requestJson("/api/bondsman/watchlists", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    setState(data);
  }

  async function deleteWatchlist(id) {
    const data = await requestJson(`/api/bondsman/watchlists/${id}`, { method: "DELETE" });
    setState(data);
  }

  async function acknowledgeAlert(id) {
    const data = await requestJson(`/api/bondsman/alerts/${id}/acknowledge`, { method: "POST", body: JSON.stringify({}) });
    setState(data);
  }

  async function claimLead(bookingId) {
    const data = await requestJson(`/api/bondsman/leads/${bookingId}/claim`, { method: "POST", body: JSON.stringify({}) });
    setState(data);
  }

  async function updateCase(item) {
    const bondAmountText = window.prompt("Bond amount in dollars", item.bondAmountCents ? String(item.bondAmountCents / 100) : "");
    if (bondAmountText === null) return;
    const courtDate = window.prompt("Court date/time (YYYY-MM-DD HH:MM:SS)", item.courtDate || "");
    if (courtDate === null) return;
    const paymentStatus = window.prompt("Payment status", item.paymentStatus || "pending");
    if (paymentStatus === null) return;
    const notes = window.prompt("Case notes", item.notes || "");
    if (notes === null) return;
    const dollars = Number.parseFloat(bondAmountText || "0");
    const data = await requestJson(`/api/bondsman/cases/${item.caseId}`, {
      method: "PATCH",
      body: JSON.stringify({
        bondAmountCents: Number.isFinite(dollars) ? Math.round(dollars * 100) : null,
        courtDate,
        paymentStatus,
        notes,
      }),
    });
    setState(data);
  }

  return html`
    <div className="space-y-6">
      ${error ? html`<div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">${error}</div>` : null}
      <section className="grid gap-4 md:grid-cols-4">
        <${StatCard} label="Open Alerts" value=${state.alerts.filter((item) => item.alertStatus === "open").length} accent="text-amber-600" />
        <${StatCard} label="Watchlist Entries" value=${state.watchlists.length} accent="text-sky-600" />
        <${StatCard} label="Open Leads" value=${state.leads.length} accent="text-emerald-600" />
        <${StatCard} label="Forfeiture Risk" value=${state.cases.filter((item) => item.isForfeitureRisk).length} accent="text-rose-600" />
      </section>

      <section className="grid gap-6 xl:grid-cols-[0.9fr_1.1fr]">
        <${WatchlistForm} onCreate=${createWatchlist} />
        <${AlertsFeed} alerts=${state.alerts} onAcknowledge=${acknowledgeAlert} />
      </section>

      <section className="grid gap-6 xl:grid-cols-[0.9fr_1.1fr]">
        <${WatchlistTable} items=${state.watchlists} onDelete=${deleteWatchlist} />
        <${LeadsPanel} leads=${state.leads} onClaim=${claimLead} />
      </section>

      <section className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
        <${CasesPanel} cases=${state.cases} onUpdate=${updateCase} />
        <${MapPanel} locations=${state.locations} />
      </section>
    </div>
  `;
}

const rootNode = document.getElementById("bondsman-command-center-root");
if (rootNode) {
  createRoot(rootNode).render(html`<${App} />`);
}
