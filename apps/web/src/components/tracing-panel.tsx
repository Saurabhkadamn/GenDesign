'use client';
import { useEffect, useState } from 'react';
import { api, post } from '@/lib/api';

type Connection = { configured: boolean; available: boolean; project?: string; url?: string; message?: string; error?: string; provider?: 'LangSmith' };

export function TracingPanel() {
  const [connection, setConnection] = useState<Connection>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    api<Connection>('admin/tracing', { signal: controller.signal }).then(setConnection)
      .catch((err: Error) => { if (!controller.signal.aborted) setError(err.message); });
    return () => controller.abort();
  }, []);
  return <section className="model-config">
    <h3>LangSmith traces</h3>
    <p className="muted-copy">Private prompts, generated source and tool results are recorded with credentials removed. Connection settings stay on the Python server.</p>
    <p role="status">{connection ? connection.available ? 'Connected' : connection.configured ? 'Configured · connection not checked' : 'Tracing is unconfigured' : 'Loading tracing status…'}</p>
    {connection?.project && <p>Project: {connection.project}</p>}
    {(error || connection?.message || connection?.error) && <p role="alert">{error || connection?.message || connection?.error}</p>}
    <button className="primary-btn" disabled={busy || !connection?.configured} onClick={async () => {
      setBusy(true); setError('');
      try { setConnection(await post<Connection>('admin/tracing/test')); }
      catch (err) { setError((err as Error).message); }
      finally { setBusy(false); }
    }}>{busy ? 'Checking…' : 'Check LangSmith connection'}</button>
    {connection?.available && connection.url && <a className="subtle-btn" href={connection.url} target="_blank" rel="noreferrer">Open trace project ↗</a>}
  </section>;
}
