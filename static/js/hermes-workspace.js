import { h, render } from 'https://esm.sh/preact@10.19.3';
import { useState, useEffect, useRef } from 'https://esm.sh/preact@10.19.3/hooks';
import htm from 'https://esm.sh/htm@3.1.1';

const html = htm.bind(h);

function getCsrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.content : '';
}

function formatTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function getStatusDot(status) {
  const s = (status || '').toLowerCase();
  if (s === 'online' || s === 'active' || s === 'running') return 'online';
  if (s === 'warning' || s === 'degraded') return 'warning';
  if (s === 'error' || s === 'failed' || s === 'offline') return 'error';
  return 'idle';
}

function getFeedClass(stage) {
  const s = (stage || '').toLowerCase();
  if (s.includes('complete') || s.includes('success')) return 'success';
  if (s.includes('error') || s.includes('fail')) return 'error';
  if (s.includes('warn')) return 'warning';
  return 'info';
}

function AgentSidebar({ agents, connected, wsUrl }) {
  return html`
    <div class="hw-panel" style="padding:12px; height:100%; display:flex; flex-direction:column;">
      <div class="hw-sidebar-header">Agents</div>
      <div style="font-size:0.6rem; color:#5C554C; margin-bottom:10px;">
        ${connected ? html`<span style="color:#4CAF50;">●</span> Connected` : html`<span style="color:#C94A3A;">●</span> Disconnected`}
        <span style="margin-left:8px; color:#3A3530;">|</span>
        <span style="margin-left:8px;">${wsUrl}</span>
      </div>
      <div class="hw-scroll" style="flex:1;">
        ${agents.length === 0 && html`
          <div style="color:#5C554C; font-size:0.72rem; padding:8px;">No agents reported yet.</div>
        `}
        ${agents.map(a => html`
          <div key=${a.agent_id} class="hw-agent-row">
            <div class="hw-agent-dot ${getStatusDot(a.status)}"></div>
            <div style="flex:1; min-width:0;">
              <div style="font-size:0.75rem; color:#E8DFD0; font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${a.agent_name || a.agent_id}</div>
              <div style="font-size:0.6rem; color:#8A7F72; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${a.message || a.status || 'idle'}</div>
            </div>
            ${a.requires_approval && html`<span style="font-size:0.55rem; color:#D4892A; background:rgba(212,137,42,0.12); padding:2px 6px; border-radius:3px;">APPROVE</span>`}
          </div>
        `)}
      </div>
    </div>
  `;
}

function FeedPanel({ items }) {
  const bottomRef = useRef(null);
  useEffect(() => {
    if (bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [items.length]);

  return html`
    <div class="hw-panel" style="padding:12px; height:100%; display:flex; flex-direction:column;">
      <div class="hw-sidebar-header">Live Feed</div>
      <div class="hw-scroll" style="flex:1; display:flex; flex-direction:column; gap:4px;">
        ${items.length === 0 && html`
          <div style="color:#5C554C; font-size:0.72rem; padding:8px;">Waiting for events...</div>
        `}
        ${items.map((item, i) => html`
          <div key=${i} class="hw-feed-item ${getFeedClass(item.stage)}">
            <div style="display:flex; justify-content:space-between; gap:8px;">
              <span style="color:#E8DFD0; font-weight:600;">${item.agent_name || item.agent_id || 'System'}</span>
              <span style="color:#5C554C; font-size:0.6rem; white-space:nowrap;">${formatTime(item.created_at)}</span>
            </div>
            <div style="margin-top:2px;">${item.message || item.stage || ''}</div>
            ${item.status && html`<div style="font-size:0.6rem; color:#8A7F72; margin-top:2px;">status: ${item.status}</div>`}
          </div>
        `)}
        <div ref=${bottomRef}></div>
      </div>
    </div>
  `;
}

function ChatPanel({ messages, onSend, pendingAction, onConfirm, onCancel, loading }) {
  const [input, setInput] = useState('');
  const bottomRef = useRef(null);

  useEffect(() => {
    if (bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages.length, pendingAction]);

  const handleSubmit = (e) => {
    e.preventDefault();
    const q = input.trim();
    if (!q || loading) return;
    onSend(q);
    setInput('');
  };

  return html`
    <div class="hw-panel" style="padding:12px; height:100%; display:flex; flex-direction:column;">
      <div class="hw-sidebar-header" style="display:flex; justify-content:space-between; align-items:center;">
        <span>AI Console</span>
        <span style="font-size:0.55rem; color:#5C554C; font-weight:400;">Kimi K2.6</span>
      </div>
      <div class="hw-scroll" style="flex:1; margin-bottom:12px;">
        <div class="hw-chat-messages">
          ${messages.length === 0 && html`
            <div style="color:#5C554C; font-size:0.8rem; padding:16px 8px; text-align:center;">
              Ask about records, missing persons, blog posts, subscribers, or request a draft.
            </div>
          `}
          ${messages.map((msg, i) => html`
            <div key=${i} class="${msg.role === 'user' ? 'hw-msg-user' : msg.role === 'tool' ? 'hw-msg-tool' : 'hw-msg-assistant'}">
              ${msg.content}
            </div>
          `)}
          ${pendingAction && html`
            <div class="hw-pending-box">
              <div style="font-size:0.75rem; color:#D4892A; font-weight:600; margin-bottom:6px;">Action Requires Confirmation</div>
              <div style="font-size:0.72rem; color:#C5BCAE; margin-bottom:8px;">${pendingAction.summary || pendingAction.tool_name}</div>
              <div style="display:flex; gap:8px;">
                <button class="hw-btn" onClick=${() => onConfirm(pendingAction.token)}>Confirm</button>
                <button class="hw-btn-ghost" onClick=${onCancel}>Cancel</button>
              </div>
            </div>
          `}
          ${loading && html`
            <div class="hw-msg-assistant" style="opacity:0.6;">
              <span style="display:inline-block; animation: hw-pulse 1.2s infinite;">Thinking…</span>
            </div>
          `}
          <div ref=${bottomRef}></div>
        </div>
      </div>
      <form onSubmit=${handleSubmit} style="display:flex; gap:8px;">
        <input
          class="hw-input"
          type="text"
          placeholder="Ask the admin AI..."
          value=${input}
          onInput=${e => setInput(e.target.value)}
          disabled=${loading}
        />
        <button type="submit" class="hw-btn" disabled=${loading || !input.trim()}>Send</button>
      </form>
    </div>
  `;
}

function HermesWorkspace() {
  const [messages, setMessages] = useState([]);
  const [agents, setAgents] = useState([]);
  const [feedItems, setFeedItems] = useState([]);
  const [pendingAction, setPendingAction] = useState(null);
  const [loading, setLoading] = useState(false);
  const [connected, setConnected] = useState(false);
  const [wsUrl, setWsUrl] = useState('');
  const wsRef = useRef(null);
  const reconnectTimeoutRef = useRef(null);

  useEffect(() => {
    const raw = document.getElementById('hw-bootstrap');
    let bootstrap = {};
    if (raw) {
      try { bootstrap = JSON.parse(raw.textContent); } catch (_) {}
    }
    setAgents(bootstrap.agents || []);
    setFeedItems(bootstrap.feed_items || []);
    setWsUrl(bootstrap.ws_url || '/ws/agents');

    const connectWs = () => {
      if (wsRef.current) return;
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const url = `${protocol}//${window.location.host}${bootstrap.ws_url || '/ws/agents'}`;
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
      };

      ws.onmessage = (event) => {
        let data;
        try { data = JSON.parse(event.data); } catch (_) { return; }
        if (data.type === 'snapshot') {
          setAgents(data.agents || []);
          setFeedItems(data.feed_items || []);
        } else if (data.type === 'event') {
          const evt = data.event || data;
          setFeedItems(prev => [evt, ...prev].slice(0, 200));
          if (evt.agent_id) {
            setAgents(prev => {
              const idx = prev.findIndex(a => a.agent_id === evt.agent_id);
              if (idx >= 0) {
                const next = [...prev];
                next[idx] = { ...next[idx], ...evt };
                return next;
              }
              return [...prev, evt];
            });
          }
        } else if (data.agent_id) {
          // Raw event payload from Redis pubsub
          setFeedItems(prev => [data, ...prev].slice(0, 200));
          setAgents(prev => {
            const idx = prev.findIndex(a => a.agent_id === data.agent_id);
            if (idx >= 0) {
              const next = [...prev];
              next[idx] = { ...next[idx], ...data };
              return next;
            }
            return [...prev, data];
          });
        }
      };

      ws.onclose = () => {
        setConnected(false);
        wsRef.current = null;
        reconnectTimeoutRef.current = setTimeout(connectWs, 3000);
      };

      ws.onerror = () => {
        ws.close();
      };
    };

    connectWs();
    return () => {
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, []);

  const sendChat = async (question) => {
    setMessages(prev => [...prev, { role: 'user', content: question }]);
    setLoading(true);
    try {
      const res = await fetch('/admin/workspace/chat', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCsrfToken(),
        },
        body: JSON.stringify({ question }),
      });
      const data = await res.json();
      if (res.ok) {
        const transcript = data.transcript || [];
        setMessages(prev => [...prev, ...transcript.map(t => ({ role: t.role || 'assistant', content: t.content || '' }))]);
        if (data.pending_action) {
          setPendingAction(data.pending_action);
        } else {
          setPendingAction(null);
        }
      } else {
        setMessages(prev => [...prev, { role: 'assistant', content: data.error || 'Request failed.' }]);
      }
    } catch (err) {
      setMessages(prev => [...prev, { role: 'assistant', content: 'Network error. Please try again.' }]);
    } finally {
      setLoading(false);
    }
  };

  const confirmAction = async (token) => {
    setLoading(true);
    try {
      const res = await fetch('/admin/workspace/confirm', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCsrfToken(),
        },
        body: JSON.stringify({ token }),
      });
      const data = await res.json();
      if (res.ok) {
        setMessages(prev => [...prev, { role: 'assistant', content: data.message || 'Action completed.' }]);
        setPendingAction(null);
      } else {
        setMessages(prev => [...prev, { role: 'assistant', content: data.error || 'Confirmation failed.' }]);
      }
    } catch (err) {
      setMessages(prev => [...prev, { role: 'assistant', content: 'Network error during confirmation.' }]);
    } finally {
      setLoading(false);
    }
  };

  const cancelAction = async () => {
    try {
      await fetch('/admin/workspace/cancel', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCsrfToken(),
        },
        body: JSON.stringify({}),
      });
    } catch (_) {}
    setPendingAction(null);
    setMessages(prev => [...prev, { role: 'assistant', content: 'Action cancelled.' }]);
  };

  return html`
    <div style="display:grid; grid-template-columns: 240px 1fr 300px; gap:12px; height:calc(100vh - 100px); min-height:500px;">
      <div style="min-width:0;">
        <${AgentSidebar} agents=${agents} connected=${connected} wsUrl=${wsUrl} />
      </div>
      <div style="min-width:0;">
        <${ChatPanel}
          messages=${messages}
          onSend=${sendChat}
          pendingAction=${pendingAction}
          onConfirm=${confirmAction}
          onCancel=${cancelAction}
          loading=${loading}
        />
      </div>
      <div style="min-width:0;">
        <${FeedPanel} items=${feedItems} />
      </div>
    </div>
  `;
}

const root = document.getElementById('hermes-workspace-root');
if (root) {
  render(html`<${HermesWorkspace} />`, root);
}
