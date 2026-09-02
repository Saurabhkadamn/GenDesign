'use client';
export default function ErrorPage({ reset }: { reset: () => void }) {
  return (
    <main className="auth-page">
      <div className="auth-card">
        <h1>Let’s reconnect.</h1>
        <p>The workspace couldn’t be loaded. Saved revisions are safe.</p>
        <button className="primary-btn" onClick={reset}>
          Try again
        </button>
      </div>
    </main>
  );
}
