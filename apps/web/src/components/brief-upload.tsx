'use client';
import { useRef, useState } from 'react';
import { post } from '@/lib/api';

export function BriefUpload({ projectId, disabled, onStored }: {
  projectId?: string; disabled: boolean; onStored: (title: string, id: string) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  return <div>
    <button type="button" disabled={disabled || busy || !projectId} onClick={() => input.current?.click()}>
      {busy ? 'Storing brief…' : 'Attach full brief'}
    </button>
    <input ref={input} type="file" accept=".txt,.md,text/plain,text/markdown" hidden onChange={async (event) => {
      const file = event.target.files?.[0];
      event.target.value = '';
      if (!file || !projectId) return;
      setError(''); setBusy(true);
      try {
        if (file.size > 1_000_000) throw new Error('Use a text or Markdown brief up to 1 MB.');
        const content = await file.text();
        const result = await post<{sourceId: string}>(`projects/${projectId}/briefs`, {
          title: file.name, content, idempotencyKey: crypto.randomUUID(),
        });
        onStored(file.name, result.sourceId);
      } catch (err) { setError((err as Error).message); }
      finally { setBusy(false); }
    }} />
    {error && <p role="alert">{error}</p>}
  </div>;
}
