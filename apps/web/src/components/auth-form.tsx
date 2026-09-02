'use client';
import { useState } from 'react';
import { ArrowUpRight, LoaderCircle, ShieldCheck } from 'lucide-react';
import { post } from '@/lib/api';

export function AuthForm({
  changePassword = false,
  serverError,
}: {
  changePassword?: boolean;
  serverError?: string;
}) {
  const [error, setError] = useState(serverError ?? '');
  const [busy, setBusy] = useState(false);
  return (
    <main className="auth-page">
      <div className="auth-brand">
        <span className="forma-symbol">f</span>
        <span>
          forma<span className="wordmark-dot">.</span>
        </span>
      </div>
      <div className="auth-card">
        <span className="eyebrow">YOUR ENGINEERING WORKSPACE</span>
        <h1>{changePassword ? 'Make it your own.' : 'Good ideas take shape here.'}</h1>
        <p>
          {changePassword
            ? 'Set a new password before opening your workspace.'
            : 'Sign in to turn a conversation into something you can build.'}
        </p>
        <form
          onSubmit={async (event) => {
            event.preventDefault();
            setBusy(true);
            setError('');
            const data = new FormData(event.currentTarget);
            try {
              await post(
                changePassword ? 'auth/password' : 'auth/login',
                changePassword
                  ? { password: data.get('password') }
                  : { email: data.get('email'), password: data.get('password') },
              );
              window.location.reload();
            } catch (err) {
              setError((err as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          {!changePassword && (
            <label>
              Email address
              <input
                name="email"
                type="email"
                autoComplete="email"
                placeholder="you@studio.com"
                required
              />
            </label>
          )}
          <label>
            {changePassword ? 'New password' : 'Password'}
            <input
              name="password"
              type="password"
              minLength={changePassword ? 12 : 1}
              autoComplete={changePassword ? 'new-password' : 'current-password'}
              required
            />
          </label>
          {changePassword && <small>Use at least 12 characters.</small>}
          {error && (
            <div className="error-notice" role="alert">
              {error}
            </div>
          )}
          <button className="primary-btn" disabled={busy}>
            {busy ? <LoaderCircle className="spin" size={16} /> : <ArrowUpRight size={17} />}{' '}
            {changePassword ? 'Save password' : 'Open workspace'}
          </button>
        </form>
        <div className="auth-note">
          <ShieldCheck size={15} /> Private access · Accounts are provided by your administrator.
        </div>
      </div>
      <div className="auth-footer">Thoughtfully engineered. One conversation at a time.</div>
    </main>
  );
}
