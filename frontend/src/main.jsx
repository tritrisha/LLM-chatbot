import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';

const backend = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/$/, '');
const sessionId = `s-${crypto.randomUUID()}`;

function Reply({ reply, metadata }) {
  return (
    <article className="reply" data-testid="assistant-message">
      <div className="author"><span className="mark" aria-hidden="true">✦</span> Relay</div>
      <p className="text">{reply}</p>
      <dl className="metadata">
        <div><dt>Source</dt><dd data-testid="meta-source">{metadata.resolution_source}</dd></div>
        <div><dt>Model</dt><dd data-testid="meta-model">{metadata.model}</dd></div>
        <div><dt>Latency</dt><dd data-testid="meta-latency">{metadata.latency_ms} ms</dd></div>
        <div><dt>Input tokens</dt><dd data-testid="meta-input-tokens">{metadata.input_tokens}</dd></div>
        <div><dt>Output tokens</dt><dd data-testid="meta-output-tokens">{metadata.output_tokens}</dd></div>
        <div><dt>Key used</dt><dd data-testid="meta-key-id">{metadata.key_id ?? 'None (cached)'}</dd></div>
        <div><dt>Failed keys</dt><dd data-testid="meta-failed-keys">{metadata.fallback_attempts}</dd></div>
      </dl>
    </article>
  );
}

function App() {
  const [message, setMessage] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [turns, setTurns] = useState([]);
  const [pending, setPending] = useState(false);
  const working = useRef(false);
  const bottom = useRef(null);

  useEffect(() => {
    if (turns.length) bottom.current?.scrollIntoView({ block: 'nearest' });
  }, [turns, pending]);

  async function send(event) {
    event.preventDefault();
    const text = message.trim();
    if (!text || working.current) return;

    working.current = true;
    setPending(true);
    setMessage('');
    setTurns(previous => [...previous, { text }]);
    const finish = (result) => setTurns(previous => [
      ...previous.slice(0, -1), { ...previous.at(-1), ...result },
    ]);

    try {
      const response = await fetch(`${backend}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          message: text,
          ...(systemPrompt.trim() ? { system_prompt: systemPrompt.trim() } : {}),
        }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        const reason = response.status === 503 && data?.detail === 'all providers unavailable'
          ? 'Reply unavailable. All provider keys failed. Please try again.'
          : typeof data?.detail === 'string'
            ? data.detail
            : data?.detail?.[0]?.msg ?? 'The chat service could not process your message.';
        throw new Error(reason);
      }
      if (!data?.reply || !data?.metadata) throw new Error('The chat service returned an incomplete reply.');
      finish({ data });
    } catch (error) {
      finish({ error: error instanceof TypeError ? 'Could not reach the chat service. Please try again.' : error.message });
    } finally {
      working.current = false;
      setPending(false);
    }
  }

  return (
    <>
      <header className="top">
        <div className="top-inner">
          <div className="brand"><span className="mark" aria-hidden="true">✦</span> Relay Chat</div>
          <details className="settings">
            <summary>Session settings</summary>
            <label>
              System prompt
              <textarea
                data-testid="system-prompt-input"
                rows="3"
                value={systemPrompt}
                placeholder="Use the current session prompt"
                onChange={event => setSystemPrompt(event.target.value)}
              />
            </label>
          </details>
        </div>
      </header>
      <main className="chat">
        <section className="conversation" aria-label="Conversation" aria-live="polite">
          {turns.length === 0 && (
            <div className="welcome">
              <span className="mark" aria-hidden="true">✦</span>
              <h1>What’s on your mind?</h1>
              <p>Start a conversation. Each reply shows the model, response time, and token usage.</p>
            </div>
          )}
          {turns.map(({ text, data, error }, index) => (
            <div className="turn" key={index}>
              <p className="user text">{text}</p>
              {data && <Reply {...data} />}
              {error && <p className="error" role="alert">{error}</p>}
            </div>
          ))}
          {pending && <p className="pending" role="status">Waiting for Relay…</p>}
          <div ref={bottom} />
        </section>
        <form className="composer" onSubmit={send}>
          <textarea
            data-testid="chat-input"
            aria-label="Message"
            placeholder="Message Relay"
            rows="2"
            maxLength={4000}
            value={message}
            onChange={event => setMessage(event.target.value)}
            onKeyDown={event => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                event.currentTarget.form.requestSubmit();
              }
            }}
          />
          <button data-testid="send-button" disabled={pending || !message.trim()}>Send</button>
        </form>
      </main>
    </>
  );
}

createRoot(document.getElementById('root')).render(<App />);
