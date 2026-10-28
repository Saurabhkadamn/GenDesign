'use client';
import { useState } from 'react';
import { api } from '@/lib/api';
import type { WorkspaceState } from '@forma/core';

export function DraftDownloads({ draft }: { draft: NonNullable<WorkspaceState['draft']> }) {
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const artifacts = draft.artifacts;
  if (!artifacts.length) return null;
  return <section className="activity requirement-checks" aria-label="Draft files">
    <h3>Built files are available</h3>
    <p>{draft.current ? 'Inspect this draft while engineering work continues.'
      : 'These files are from an earlier build. Recent changes still need verification.'} Downloads do not mean the requirements have passed.</p>
    <details><summary>Download draft files ({artifacts.length})</summary>
      <ul>{artifacts.map((file) => <li key={file.name}>
        <button className="subtle-btn" disabled={busy !== null} onClick={async () => {
          setError(''); setBusy(file.name);
          try {
            const query = new URLSearchParams({ name: file.name, candidate: String(draft.candidateHash) });
            const result = await api<{ url: string }>(`runs/${draft.runId}/draft-artifact?${query}`);
            const link = document.createElement('a');
            link.href = result.url; link.download = file.name; link.rel = 'noopener';
            link.target = '_blank'; link.click();
          } catch (err) { setError((err as Error).message); }
          finally { setBusy(null); }
        }}>{busy === file.name ? 'Preparing download…' : file.name}</button>
      </li>)}</ul>
    </details>
    {error && <p role="alert">{error}</p>}
  </section>;
}
