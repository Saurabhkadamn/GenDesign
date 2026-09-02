'use client';
import { useEffect, useState } from 'react';
import type { Profile } from '@forma/core';
import { Workspace } from '@/components/workspace';
import { AuthForm } from '@/components/auth-form';
import { api } from '@/lib/api';

export default function Page() {
  const [session, setSession] = useState<{ profile: Profile | null; configured: boolean }>();
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    api<{ profile: Profile | null; configured: boolean }>('session', { signal: controller.signal })
      .then(setSession).catch((err: Error) => {
        if (!controller.signal.aborted) setError(err.message);
      });
    return () => controller.abort();
  }, []);
  if (error) return <main className="auth-page"><div className="auth-card" role="alert"><h1>Connection interrupted.</h1><p>{error}</p><button className="primary-btn" onClick={() => window.location.reload()}>Try again</button></div></main>;
  if (!session) return <main className="auth-page" aria-busy="true"><p role="status">Opening your workspace…</p></main>;
  if (!session.configured) return <Workspace profile={null} configured={false} />;
  if (!session.profile) return <AuthForm />;
  return session.profile.must_change_password ? <AuthForm changePassword /> : <Workspace profile={session.profile} configured />;
}
