(function () {
  'use strict';

  const STORAGE_KEY_HISTORY = 'mb-chat-history';

  let open = false;
  let messages = [];
  let sending = false;
  let online = true;

  function loadHistory() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY_HISTORY);
      if (raw) messages = JSON.parse(raw).slice(-50);
    } catch (e) {
      messages = [];
    }
  }

  function saveHistory() {
    try {
      localStorage.setItem(STORAGE_KEY_HISTORY, JSON.stringify(messages.slice(-50)));
    } catch (e) {
      // ignore
    }
  }

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (k === 'className') node.className = attrs[k];
        else if (k === 'innerHTML') node.innerHTML = attrs[k];
        else if (k.startsWith('on') && typeof attrs[k] === 'function') {
          node.addEventListener(k.slice(2).toLowerCase(), attrs[k]);
        } else {
          node.setAttribute(k, attrs[k]);
        }
      });
    }
    if (children) {
      children.forEach(function (c) {
        if (typeof c === 'string') node.appendChild(document.createTextNode(c));
        else if (c) node.appendChild(c);
      });
    }
    return node;
  }

  function renderWidget() {
    const existing = document.getElementById('mb-chat-widget');
    if (existing) existing.remove();

    const container = el('div', { id: 'mb-chat-widget' });

    if (!open) {
      container.appendChild(
        el('button', {
          className: 'fixed bottom-4 right-4 z-50 flex h-14 w-14 items-center justify-center rounded-full bg-[#1e3a8a] text-white shadow-lg hover:bg-[#1e40af] active:scale-95 transition',
          'aria-label': 'Open Montana Blotter assistant',
          onClick: function () {
            open = true;
            if (messages.length === 0) {
              messages.push({
                role: 'assistant',
                content: "Hi — I'm the Montana Blotter assistant. I can help you find public records by county or city, explain our data sources, or point you to jail rosters and court records. What are you looking for?",
              });
              saveHistory();
            }
            renderWidget();
          },
        }, [
          el('svg', { width: '24', height: '24', viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
            el('path', { d: 'M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z' }),
          ]),
        ])
      );
    } else {
      const panel = el('div', {
        className: 'fixed bottom-4 right-4 z-50 flex h-[min(600px,80vh)] w-[min(400px,calc(100vw-2rem))] flex-col rounded-lg border border-stone-200 bg-white shadow-2xl',
        role: 'dialog',
        'aria-label': 'Montana Blotter assistant',
      });

      const header = el('div', {
        className: 'flex items-center justify-between rounded-t-lg bg-[#1e3a8a] px-4 py-3 text-white',
      }, [
        el('div', { className: 'flex items-center gap-2' }, [
          el('div', { className: 'flex h-8 w-8 items-center justify-center rounded-full bg-white/20 text-sm font-bold' }, ['MB']),
          el('div', {}, [
            el('div', { className: 'text-sm font-semibold' }, ['Blotter Assistant']),
            el('div', { className: 'text-xs opacity-80' }, [online ? 'Online' : 'Offline']),
          ]),
        ]),
        el('div', { className: 'flex items-center gap-1' }, [
          el('button', {
            className: 'rounded p-1.5 text-white/80 hover:bg-white/10',
            'aria-label': 'Clear chat',
            title: 'Clear chat',
            onClick: clearChat,
          }, [
            el('svg', { width: '16', height: '16', viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
              el('path', { d: 'M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2' }),
            ]),
          ]),
          el('button', {
            className: 'rounded p-1.5 text-white/80 hover:bg-white/10',
            'aria-label': 'Close chat',
            onClick: function () { open = false; renderWidget(); },
          }, [
            el('svg', { width: '18', height: '18', viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
              el('path', { d: 'M18 6L6 18M6 6l12 12' }),
            ]),
          ]),
        ]),
      ]);

      const messagesEl = el('div', { className: 'flex-1 overflow-y-auto px-4 py-3 text-sm space-y-3' });
      if (messages.length === 0) {
        messagesEl.appendChild(el('div', { className: 'text-center text-stone-500' }, ['Ask about Montana public records, jail rosters, or court data.']));
      } else {
        messages.forEach(function (m) {
          messagesEl.appendChild(renderMessage(m));
        });
      }

      const input = el('textarea', {
        className: 'flex-1 resize-none rounded-md border border-stone-300 bg-white px-3 py-2 text-sm focus:border-[#1e3a8a] focus:outline-none',
        rows: '1',
        placeholder: 'Ask about records...',
        maxLength: '2000',
      });

      const form = el('form', {
        className: 'flex items-end gap-2 border-t border-stone-200 px-3 py-3',
        onSubmit: function (e) {
          e.preventDefault();
          const text = input.value.trim();
          if (!text || sending || !online) return;
          input.value = '';
          sendMessage(text);
        },
      }, [
        input,
        el('button', {
          type: 'submit',
          className: 'flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-[#1e3a8a] text-white hover:bg-[#1e40af] disabled:opacity-50',
          'aria-label': 'Send message',
        }, [
          el('svg', { width: '16', height: '16', viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
            el('path', { d: 'M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z' }),
          ]),
        ]),
      ]);

      input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          form.dispatchEvent(new Event('submit'));
        }
      });

      panel.appendChild(header);
      panel.appendChild(messagesEl);
      panel.appendChild(form);
      container.appendChild(panel);

      setTimeout(function () {
        messagesEl.scrollTop = messagesEl.scrollHeight;
        input.focus();
      }, 0);
    }

    document.body.appendChild(container);
  }

  function renderMessage(m) {
    const isUser = m.role === 'user';
    return el('div', { className: 'flex ' + (isUser ? 'justify-end' : 'justify-start') }, [
      el('div', {
        className: 'max-w-[85%] rounded-2xl px-3 py-2 text-sm leading-relaxed ' + (isUser ? 'bg-[#1e3a8a] text-white' : 'bg-stone-100 text-stone-900'),
      }, m.streaming && !m.content ? [
        el('span', { className: 'inline-flex gap-1' }, [
          el('span', { className: 'h-1.5 w-1.5 animate-bounce rounded-full bg-current', style: 'animation-delay:-0.3s' }),
          el('span', { className: 'h-1.5 w-1.5 animate-bounce rounded-full bg-current', style: 'animation-delay:-0.15s' }),
          el('span', { className: 'h-1.5 w-1.5 animate-bounce rounded-full bg-current' }),
        ]),
      ] : [renderMarkdown(m.content)]),
    ]);
  }

  function renderMarkdown(text) {
    const wrapper = el('span', {});
    const parts = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*|\[[^\]]+\]\([^)]+\))/g);
    parts.forEach(function (part) {
      if (part.startsWith('**') && part.endsWith('**')) {
        wrapper.appendChild(el('strong', {}, [part.slice(2, -2)]));
      } else if (part.startsWith('*') && part.endsWith('*')) {
        wrapper.appendChild(el('em', {}, [part.slice(1, -1)]));
      } else if (part.startsWith('[') && part.includes('](')) {
        const match = part.match(/\[([^\]]+)\]\(([^)]+)\)/);
        if (match) {
          wrapper.appendChild(el('a', { href: match[2], target: '_blank', rel: 'noopener noreferrer', className: 'underline' }, [match[1]]));
        } else {
          wrapper.appendChild(document.createTextNode(part));
        }
      } else {
        part.split('\n').forEach(function (line, i, arr) {
          wrapper.appendChild(document.createTextNode(line));
          if (i < arr.length - 1) wrapper.appendChild(el('br'));
        });
      }
    });
    return wrapper;
  }

  function clearChat() {
    messages = [];
    saveHistory();
    renderWidget();
  }

  function sendMessage(text) {
    messages.push({ role: 'user', content: text });
    const assistantId = 'a-' + Date.now();
    messages.push({ role: 'assistant', content: '', streaming: true, id: assistantId });
    sending = true;
    saveHistory();
    renderWidget();

    fetch('/api/chat/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: messages.filter(function (m) { return !m.streaming; }) }),
    })
      .then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';
        let assembled = '';

        function read() {
          return reader.read().then(function (result) {
            if (result.done) {
              finalize(assistantId, assembled || 'Sorry, I could not generate a response.');
              return;
            }
            buf += decoder.decode(result.value, { stream: true });
            let idx;
            while ((idx = buf.indexOf('\n\n')) !== -1) {
              const block = buf.slice(0, idx);
              buf = buf.slice(idx + 2);
              const lines = block.split('\n');
              let data = '';
              lines.forEach(function (line) {
                if (line.startsWith('data: ')) data += line.slice(6);
              });
              if (!data || data === '[DONE]') continue;
              try {
                const parsed = JSON.parse(data);
                if (parsed.error) {
                  finalize(assistantId, 'Error: ' + parsed.error);
                  return;
                }
                if (parsed.text) {
                  assembled += parsed.text;
                  updateAssistant(assistantId, assembled);
                }
              } catch (e) {
                // ignore
              }
            }
            return read();
          });
        }
        return read();
      })
      .catch(function (err) {
        finalize(assistantId, 'Sorry, the connection failed. Please try again.');
        console.error('[mb-chat]', err);
      });
  }

  function updateAssistant(id, text) {
    messages = messages.map(function (m) {
      return m.id === id ? { ...m, content: text, streaming: true } : m;
    });
    saveHistory();
    renderWidget();
  }

  function finalize(id, text) {
    messages = messages.map(function (m) {
      return m.id === id ? { role: 'assistant', content: text, streaming: false } : m;
    });
    sending = false;
    saveHistory();
    renderWidget();
  }

  function init() {
    fetch('/api/chat/', { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (data) { online = Boolean(data.online); renderWidget(); })
      .catch(function () { online = false; renderWidget(); });

    loadHistory();
    renderWidget();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
