'use client';
import { useEffect, useState } from 'react';
import {
  Check,
  KeyRound,
  LoaderCircle,
  Play,
  Plus,
  ShieldCheck,
  Trash2,
  Users,
  Zap,
} from 'lucide-react';
import { api, post } from '@/lib/api';
import { TracingPanel } from './tracing-panel';
import {
  defaultLimits,
  type AgentRole,
  type AppSettings,
  type ModelConfigView,
  type ModelOptions,
  type Profile,
} from '@forma/core';

export function AdminPanel() {
  const [tab, setTab] = useState<'models' | 'controls' | 'users' | 'tracing'>('models');
  const [models, setModels] = useState<ModelConfigView[]>([]);
  const [modelOptions, setModelOptions] = useState<ModelOptions | null>(null);
  const [catalogNotice, setCatalogNotice] = useState('');
  const [users, setUsers] = useState<Profile[]>([]);
  const [settings, setSettings] = useState<AppSettings>({
    emergencyStop: false,
    engineeringEnabled: true,
    surfacingEnabled: true,
    limits: defaultLimits,
  });
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [loaded, setLoaded] = useState(false);
  const [resetUser, setResetUser] = useState<string | null>(null);
  async function load() {
    const [m, s, u] = await Promise.all([
      api<ModelConfigView[]>('admin/models'),
      api<AppSettings>('admin/settings'),
      api<Profile[]>('admin/users'),
    ]);
    setModels(m);
    setSettings(s);
    setUsers(u);
    setLoaded(true);
  }
  useEffect(() => {
    let active = true;
    // Catalog outages must not prevent account management or emergency-stop access.
    api<ModelOptions>('admin/model-options')
      .then((options) => {
        if (active) setModelOptions(options);
      })
      .catch(() => {
        if (active)
          setCatalogNotice(
            'The model catalog is unavailable. You can still enter an exact model ID and test it.',
          );
      });
    Promise.all([
      api<ModelConfigView[]>('admin/models'),
      api<AppSettings>('admin/settings'),
      api<Profile[]>('admin/users'),
    ])
      .then(([m, s, u]) => {
        if (active) {
          setModels(m);
          setSettings(s);
          setUsers(u);
          setLoaded(true);
        }
      })
      .catch((e) => {
        if (active) setNotice(e.message);
      });
    return () => {
      active = false;
    };
  }, []);
  async function act(fn: () => Promise<unknown>, message = 'Saved.') {
    setBusy(true);
    setNotice('');
    try {
      await fn();
      await load();
      setNotice(message);
    } catch (e) {
      setNotice((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="admin-panel">
      <div className="admin-tabs">
        <button className={tab === 'models' ? 'active' : ''} onClick={() => setTab('models')}>
          <KeyRound size={14} />
          Models
        </button>
        <button className={tab === 'controls' ? 'active' : ''} onClick={() => setTab('controls')}>
          <Zap size={14} />
          Execution
        </button>
        <button className={tab === 'users' ? 'active' : ''} onClick={() => setTab('users')}>
          <Users size={14} />
          Accounts
        </button>
        <button className={tab === 'tracing' ? 'active' : ''} onClick={() => setTab('tracing')}>Traces</button>
      </div>
      {tab === 'tracing' && <TracingPanel />}
      {!loaded && !notice && <p className="muted-copy">Loading settings…</p>}
      {loaded && tab === 'models' && (
        <div className="model-settings">
          <p className="muted-copy">
            Set the default connection, then optionally override it for a specialist. Models must
            pass a tool-calling test before activation.
          </p>
          {modelOptions && (
            <p className="muted-copy" role={modelOptions.syntheticNemotronTesting ? 'alert' : undefined}>
              {modelOptions.freeOnly
                ? 'Free-only testing is enabled. Paid inference is blocked on the server.'
                : 'Choose any OpenRouter model, paid or free. Usage charges depend on the model you choose.'}{' '}
              Suggestions include the full catalog. The connection test checks whether the selected model can call the CAD tools.
              {modelOptions.syntheticNemotronTesting &&
                ' Temporary Nemotron testing is enabled: NVIDIA may retain synthetic test prompts. Do not enter private project data.'}
            </p>
          )}
          {catalogNotice && (
            <p className="muted-copy" role="status">
              {catalogNotice}
            </p>
          )}
          <datalist id="openrouter-model-options">
            {modelOptions?.models.map((option) => (
              <option key={option.id} value={option.id}>
                {option.name}
              </option>
            ))}
          </datalist>
          {(['coordinator', 'cad', 'engineering'] as AgentRole[]).map((role) => {
            const model = models.find((m) => m.role === role);
            return (
              <section className="model-config" key={`${role}-${model?.version}`}>
                <div className="model-heading">
                  <strong>
                    {role === 'coordinator'
                      ? 'Default model'
                      : role === 'cad'
                        ? 'CAD specialist'
                        : 'Engineering specialist'}
                  </strong>
                  <span className={`status-chip ${model?.active && model.tested_at ? '' : 'neutral'}`}>
                    {model?.active && model.tested_at
                      ? 'Active'
                      : role !== 'coordinator'
                        ? 'Inherits default'
                        : model
                          ? 'Not active'
                          : 'Not configured'}
                  </span>
                </div>
                {model && (
                  <div className="model-summary">
                    <span>{model.model_id}</span>
                    <code>{model.key_hint}</code>
                  </div>
                )}
                {model && role !== 'coordinator' && (!model.active || !model.tested_at) && (
                  <p className="muted-copy">
                    Uses the active default model. This saved override takes effect after testing and activation.
                  </p>
                )}
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    const form = e.currentTarget;
                    const data = new FormData(form);
                    void act(async () => {
                      await post('admin/models', {
                        role,
                        modelId: data.get('modelId'),
                        apiKey: data.get('apiKey'),
                      });
                      form.reset();
                    }, 'Connection saved. Test it before activation.');
                  }}
                >
                  <label>
                    OpenRouter model ID
                    <input
                      name="modelId"
                      list="openrouter-model-options"
                      autoComplete="off"
                      placeholder="provider/model-id"
                      defaultValue={model?.model_id ?? ''}
                      required
                      maxLength={160}
                    />
                    <span className="muted-copy">Paste any OpenRouter model ID, paid or free, or choose a suggestion.</span>
                  </label>
                  <label>
                    API key
                    <input
                      name="apiKey"
                      type="password"
                      autoComplete="new-password"
                      placeholder={model ? 'Leave blank to keep the saved key' : 'Enter your OpenRouter key'}
                      required={!model}
                      minLength={10}
                    />
                  </label>
                  <div className="model-actions">
                    <button className="primary-btn" disabled={busy}>
                      Save connection
                    </button>
                    {model && (
                      <>
                        <button
                          className="subtle-btn"
                          type="button"
                          disabled={busy}
                          onClick={() =>
                            void act(
                              () => post(`admin/models/${role}/test`),
                              'Connection and tool calling passed.',
                            )
                          }
                        >
                          <Play size={12} />
                          Test
                        </button>
                        <button
                          className="subtle-btn"
                          type="button"
                          disabled={busy || !model.tested_at || model.active}
                          onClick={() =>
                            void act(
                              () => post(`admin/models/${role}/activate`),
                              'Model activated.',
                            )
                          }
                        >
                          <Check size={13} />
                          Activate
                        </button>
                        <button
                          className="icon-btn"
                          type="button"
                          aria-label={`Remove ${role} connection`}
                          disabled={busy}
                          onClick={() =>
                            void act(
                              () => api(`admin/models/${role}`, { method: 'DELETE' }),
                              'Connection removed.',
                            )
                          }
                        >
                          <Trash2 size={14} />
                        </button>
                      </>
                    )}
                  </div>
                </form>
              </section>
            );
          })}
          <div className="security-note">
            <ShieldCheck size={15} />
            Keys are encrypted on the server and cannot be read back by the browser.
          </div>
        </div>
      )}
      {loaded && tab === 'controls' && (
        <form
          className="execution-settings"
          onSubmit={(e) => {
            e.preventDefault();
            void act(() => post('admin/settings', settings), 'Execution controls saved.');
          }}
        >
          <div className={`emergency-control ${settings.emergencyStop ? 'stopped' : ''}`}>
            <div>
              <strong>Emergency stop</strong>
              <p>Pause new work and stop active runs at their next execution boundary.</p>
            </div>
            <input
              aria-label="Emergency stop"
              type="checkbox"
              checked={settings.emergencyStop}
              onChange={(e) => setSettings((s) => ({ ...s, emergencyStop: e.target.checked }))}
            />
          </div>
          <label className="toggle-row">
            Engineering calculations
            <input
              type="checkbox"
              checked={settings.engineeringEnabled}
              onChange={(e) => setSettings((s) => ({ ...s, engineeringEnabled: e.target.checked }))}
            />
          </label>
          <label className="toggle-row">
            Surface modeling
            <input
              type="checkbox"
              checked={settings.surfacingEnabled}
              onChange={(e) => setSettings((s) => ({ ...s, surfacingEnabled: e.target.checked }))}
            />
          </label>
          <div className="limits-grid">
            {(
              [
                { key: 'maxModelCalls', label: 'Model calls per request', min: 1, max: 30 },
                { key: 'maxRepairs', label: 'Automatic repair attempts', min: 0, max: 3 },
                {
                  key: 'commandTimeoutSeconds',
                  label: 'Command timeout (seconds)',
                  min: 30,
                  max: 300,
                },
                {
                  key: 'retainedExports',
                  label: 'Revisions with retained exports',
                  min: 1,
                  max: 10,
                },
                {
                  key: 'monthlySandboxSeconds',
                  label: 'Monthly sandbox seconds',
                  min: 60,
                  max: 18000,
                },
              ] as const
            ).map(({ key, label, min, max }) => (
              <label key={key}>
                {label}
                <input
                  type="number"
                  value={settings.limits[key]}
                  min={min}
                  max={max}
                  required
                  onChange={(e) =>
                    setSettings((s) => ({
                      ...s,
                      limits: { ...s.limits, [key]: Number(e.target.value) },
                    }))
                  }
                />
              </label>
            ))}
          </div>
          <p className="muted-copy">
            One active design run globally. Compute reservations are conservative application
            limits, not provider billing estimates.
          </p>
          <button className="primary-btn" disabled={busy}>
            Save execution controls
          </button>
        </form>
      )}
      {loaded && tab === 'users' && (
        <div className="account-settings">
          {users.map((user) => (
            <div className="account-row" key={user.id}>
              <span className="avatar">{user.display_name[0]}</span>
              <div>
                <strong>{user.display_name}</strong>
                <small>
                  {user.email} · {user.role}
                </small>
              </div>
              {user.role !== 'admin' && (
                <>
                  <button
                    className="subtle-btn"
                    disabled={busy}
                    onClick={() =>
                      void act(() => post(`admin/users/${user.id}`, { active: !user.active }))
                    }
                  >
                    {user.active ? 'Disable' : 'Enable'}
                  </button>
                  <button
                    className="subtle-btn"
                    disabled={busy}
                    onClick={() => setResetUser(user.id)}
                  >
                    Reset password
                  </button>
                </>
              )}
            </div>
          ))}
          {resetUser && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                const form = e.currentTarget;
                const password = new FormData(form).get('password');
                void act(async () => {
                  await post(`admin/users/${resetUser}`, { password });
                  form.reset();
                  setResetUser(null);
                }, 'Password reset. Share the temporary password securely; the engineer must change it at sign-in.');
              }}
            >
              <h3>Reset {users.find((user) => user.id === resetUser)?.display_name}’s password</h3>
              <label>
                New temporary password
                <input
                  name="password"
                  type="password"
                  autoComplete="new-password"
                  minLength={12}
                  maxLength={128}
                  required
                />
              </label>
              <div className="model-actions">
                <button className="primary-btn" disabled={busy}>
                  Reset password
                </button>
                <button type="button" className="subtle-btn" onClick={() => setResetUser(null)}>
                  Cancel
                </button>
              </div>
            </form>
          )}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const form = e.currentTarget;
              const data = new FormData(form);
              void act(async () => {
                await post('admin/users', {
                  name: data.get('name'),
                  email: data.get('email'),
                  password: data.get('password'),
                });
                form.reset();
              }, 'Account created. Share the temporary password securely.');
            }}
          >
            <h3>Invite an engineer</h3>
            <label>
              Name
              <input name="name" required maxLength={80} />
            </label>
            <label>
              Email
              <input name="email" type="email" required />
            </label>
            <label>
              Temporary password
              <input
                name="password"
                type="password"
                autoComplete="new-password"
                required
                minLength={12}
              />
            </label>
            <p className="muted-copy">
              No email is sent. The engineer must change this password before accessing projects.
            </p>
            <button className="primary-btn" disabled={busy}>
              <Plus size={14} />
              Create account
            </button>
          </form>
        </div>
      )}
      {notice && (
        <div className="notice" role="status">
          {busy && <LoaderCircle className="spin" size={14} />} {notice}
        </div>
      )}
      <div className="admin-footer">
        <span>Private testing · no automatic upgrades</span>
        <button
          className="subtle-btn"
          onClick={async () => {
            await post('auth/logout');
            window.location.reload();
          }}
        >
          Sign out
        </button>
      </div>
    </div>
  );
}
