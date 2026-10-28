'use client';
import { useState } from 'react';
import { post } from '@/lib/api';

function objects(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter((item) => item && typeof item === 'object') : [];
}

export function EngineeringStatus({ projectId, revisionId, acceptance, completion, canAccept, onAccepted }: {
  projectId: string; revisionId: string; acceptance: string;
  completion?: Record<string, unknown>; canAccept: boolean; onAccepted: () => Promise<void>;
}) {
  const [decision, setDecision] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const outstanding = objects(completion?.outstanding);
  const deliverables = objects(completion?.deliverables);
  return <section className="activity requirement-checks" aria-label="Engineering status">
    <h3>{acceptance === 'accepted' ? 'Engineering acceptance recorded'
      : acceptance === 'ready_for_review' ? 'Ready for engineering review' : 'Draft available'}</h3>
    <p>{acceptance === 'draft' ? 'Engineering work remains incomplete. Files can still be inspected and downloaded.'
      : acceptance === 'ready_for_review' ? 'Recorded mandatory checks and deliverables are complete. A person must make the acceptance decision.'
        : 'An explicit engineering decision is recorded for this revision.'}</p>
    {deliverables.length > 0 && <details><summary>What exists</summary><ul>
      {deliverables.map((item, i) => <li key={String(item.id ?? i)}>{String(item.description ?? item.name)} · {String(item.status)}</li>)}
    </ul></details>}
    {outstanding.length > 0 && <details open><summary>What remains</summary><ul>
      {outstanding.map((item, i) => <li key={`${String(item.id)}-${i}`}>
        {String(item.description ?? item.reason ?? item.id)} · {String(item.status)}
      </li>)}
    </ul></details>}
    {canAccept && acceptance === 'ready_for_review' && <form onSubmit={async (event) => {
      event.preventDefault(); setBusy(true); setError('');
      try {
        await post(`projects/${projectId}/revisions/${revisionId}/accept`, { decision });
        await onAccepted();
      } catch (err) { setError((err as Error).message); }
      finally { setBusy(false); }
    }}>
      <label>Engineering acceptance decision
        <textarea value={decision} onChange={(event) => setDecision(event.target.value)} minLength={8} maxLength={4000}
          placeholder="Record your review and the intended engineering use." required />
      </label>
      <button type="submit" disabled={busy || decision.trim().length < 8}>Record acceptance</button>
    </form>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
