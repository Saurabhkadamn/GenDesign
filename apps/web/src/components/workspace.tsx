'use client';
import dynamic from 'next/dynamic';
import Link from 'next/link';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowDownToLine,
  ArrowUp,
  ArrowUpRight,
  Box,
  Boxes,
  Check,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Command,
  Eye,
  EyeOff,
  FileBox,
  Focus,
  FolderOpen,
  Grid2X2,
  History,
  Layers3,
  LoaderCircle,
  Maximize,
  MessageSquare,
  MoreHorizontal,
  Plus,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
  Square,
  X,
  Calculator,
  CircleDot,
  Move3D,
  MousePointer2,
  RotateCcw,
} from 'lucide-react';
import { MessageResponse } from '@/components/ai-elements/message';
import {
  Conversation,
  ConversationContent,
  ConversationScrollButton,
} from '@/components/ai-elements/conversation';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { TooltipProvider, Tooltip, TooltipTrigger, TooltipContent } from '@/components/ui/tooltip';
import { api, post } from '@/lib/api';
import {
  emptyManifest,
  type Artifact,
  type Profile,
  type Project,
  type WorkspaceState,
  type RunEvent,
} from '@forma/core';
import { AdminPanel } from './admin-panel';

const CadViewer = dynamic(() => import('./cad-viewer').then((m) => m.CadViewer), {
  ssr: false,
  loading: () => <div className="viewer-loading">Opening 3D viewer…</div>,
});
type Modal = 'projects' | 'history' | 'settings' | 'feedback' | 'help' | null;
export function IconButton({
  label,
  children,
  onClick,
  active = false,
  disabled = false,
}: {
  label: string;
  children: React.ReactNode;
  onClick?: () => void;
  active?: boolean;
  disabled?: boolean;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          className={`icon-btn ${active ? 'active' : ''}`}
          aria-label={label}
          disabled={disabled}
          onClick={onClick}
        >
          {children}
        </button>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}
export function Workspace({
  profile,
  configured,
}: {
  profile: Profile | null;
  configured: boolean;
}) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [state, setState] = useState<WorkspaceState | null>(null);
  const [modal, setModal] = useState<Modal>(null);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<'model' | 'files'>('model');
  const [viewerTab, setViewerTab] = useState<'preview' | 'calculations'>('preview');
  const [selected, setSelected] = useState<string | null>(null);
  const [hidden, setHidden] = useState<string[]>([]);
  const [grid, setGrid] = useState(true);
  const [wireframe, setWireframe] = useState(false);
  const [fitVersion, setFitVersion] = useState(0);
  const [sidebar, setSidebar] = useState(() => typeof window === 'undefined' || !window.matchMedia('(max-width: 900px)').matches);
  const [mobilePanel, setMobilePanel] = useState<'preview' | 'chat'>('preview');
  const [projectQuery, setProjectQuery] = useState('');
  const [newName, setNewName] = useState('');
  const [feedback, setFeedback] = useState('');
  const [notice, setNotice] = useState('');
  const [previewOverride, setPreviewOverride] = useState<string | null>(null);
  const requestKey = useRef<{ message: string; key: string } | null>(null);
  const latestProject = useRef<string | null>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const project = state?.project;
  const revision = state?.revisions.find((r) => r.id === project?.current_revision_id);
  const manifest = revision?.manifest ?? emptyManifest;
  // A waiting_input run is still the current conversation. Treating it as
  // terminal here made the composer start a brand new run instead of sending
  // the answer/approval back to the paused LangGraph thread.
  const run = state?.runs.find((r) =>
    r.status === 'queued' || r.status === 'running' || r.status === 'waiting_input',
  );
  const waitingInput = run?.status === 'waiting_input' ? run : null;
  const activeRunId = run?.id;
  const paused = state?.runs[0]?.status === 'paused' ? state.runs[0] : null;
  const pausedMessage = paused
    ? state?.messages.filter((message) => message.run_id === paused.id && message.role === 'assistant').at(-1)?.content ?? ''
    : '';
  const pausedRequestUncertain = /uncertain|timed out|ambiguous/i.test(pausedMessage);
  const currentArtifacts =
    state?.artifacts.filter((a) => a.revision_id === project?.current_revision_id) ?? [];
  const preview =
    currentArtifacts.find((a) => a.id === previewOverride) ??
    currentArtifacts.find((a) => a.name === 'preview.glb');
  const selectable = manifest.instances.length
    ? manifest.instances
    : manifest.components.map((c) => ({
        id: c.id,
        name: c.name,
        definitionId: c.id,
        parentId: null,
      }));
  const selectedItem = selectable.find((c) => c.id === selected);
  function descendants(id: string): string[] {
    const ids = new Set([id]);
    for (let changed = true; changed;) {
      changed = false;
      for (const item of selectable) {
        if (item.parentId && ids.has(item.parentId) && !ids.has(item.id)) {
          ids.add(item.id);
          changed = true;
        }
      }
    }
    return [...ids];
  }
  const hiddenInstances = [...new Set(hidden.flatMap(descendants))];
  const selectedDefinition = manifest.components.find(
    (c) => c.id === (selectedItem?.definitionId ?? selected),
  );
  const fileSelection =
    currentArtifacts.find((a) => a.component_id === selectedDefinition?.id && a.kind === 'step') ??
    currentArtifacts.find((a) => a.component_id === manifest.rootComponentId && a.kind === 'step');

  const refresh = useCallback(async (id: string) => {
    const next = await api<WorkspaceState>(`projects/${id}`);
    if (latestProject.current === id) setState(next);
  }, []);
  const openProject = useCallback(
    async (id: string) => {
      latestProject.current = id;
      setLoading(true);
      setError('');
      setSelected(null);
      setHidden([]);
      setPreviewOverride(null);
      try {
        await refresh(id);
        window.history.replaceState(null, '', `/?project=${id}`);
        setModal(null);
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setLoading(false);
      }
    },
    [refresh],
  );
  useEffect(() => {
    if (!configured) return;
    let active = true;
    api<Project[]>('projects')
      .then((rows) => {
        if (!active) return;
        setProjects(rows);
        const requested = new URLSearchParams(window.location.search).get('project');
        const match = rows.find((p) => p.id === requested) ?? rows[0];
        if (match) void openProject(match.id);
      })
      .catch((e) => setError(e.message));
    return () => {
      active = false;
    };
  }, [configured, openProject]);
  useEffect(() => {
    if (!project?.id || !activeRunId) return;
    const id = project.id;
    let stopped = false;
    let connected = false;
    const events = new EventSource(`/api/runs/${activeRunId}/events`);
    events.onopen = () => { connected = true; };
    events.onerror = () => {
      connected = false;
      // EventSource retries in the browser, but a terminal event can be lost
      // during that reconnect window. Refresh immediately so a paused/failed
      // run and its recovery action become visible without waiting for the
      // normal polling interval.
      void refresh(id).catch(() => {
        if (!stopped) setError('Connection interrupted. Your run continues; reconnecting…');
      });
    };
    events.addEventListener('progress', (event) => {
      const row = JSON.parse((event as MessageEvent).data) as RunEvent;
      setState((current) => current && current.project.id === id
        ? { ...current, events: [...current.events.filter((e) => e.id !== row.id), row].sort((a, b) => a.id - b.id).slice(-100) }
        : current);
    });
    events.addEventListener('terminal', () => { events.close(); void refresh(id); });
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        await refresh(id);
      } catch {
        if (!stopped) setError('Connection interrupted. Your run continues; reconnecting…');
      }
      if (!stopped) timer = setTimeout(poll, connected ? 5000 : 1500);
    };
    timer = setTimeout(poll, 1500);
    return () => {
      stopped = true;
      events.close();
      clearTimeout(timer);
    };
  }, [project?.id, activeRunId, refresh]);
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key === 'k') {
        event.preventDefault();
        textarea.current?.focus();
      }
      if (event.key === 'Escape') {
        setSelected(null);
        if (window.matchMedia('(max-width: 900px)').matches) setSidebar(false);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  async function submit(message = draft) {
    if (!configured || submitting || (run && run.status !== 'waiting_input') || !message.trim()) return;
    setSubmitting(true);
    setError('');
    try {
      let target = project;
      if (!target) {
        target = await post<Project>('projects', { name: message.slice(0, 60) });
        latestProject.current = target.id;
        setProjects((p) => [target!, ...p]);
        await refresh(target.id);
        window.history.replaceState(null, '', `/?project=${target.id}`);
      }
      if (run?.status === 'waiting_input') {
        await post(`runs/${run.id}/resume`, { kind: 'answer', message });
      } else {
        if (requestKey.current?.message !== message)
          requestKey.current = { message, key: crypto.randomUUID() };
        await post(`projects/${target.id}/chat`, {
          message,
          baseRevisionId: target.current_revision_id,
          selectedIds: selected ? [selected] : [],
          idempotencyKey: requestKey.current.key,
        });
      }
      setDraft('');
      requestKey.current = null;
      await refresh(target.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  }
  async function download(artifact?: Artifact) {
    if (!artifact) return;
    try {
      const { url } = await api<{ url: string }>(`artifacts/${artifact.id}`);
      const response = await fetch(url);
      if (!response.ok) throw new Error('Download failed.');
      const blob = await response.blob();
      const href = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = href;
      anchor.download = artifact.name;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(href), 1000);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  function suggest(text: string) {
    setDraft(text);
    setMobilePanel('chat');
    textarea.current?.focus();
  }
  function toggleHidden(id: string) {
    setHidden((h) => (h.includes(id) ? h.filter((v) => v !== id) : [...h, id]));
  }
  async function runAction(action: 'cancel' | 'continue', id: string) {
    try {
      await post(`runs/${id}/${action}`);
      if (project) await refresh(project.id);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function restartPaused() {
    if (!project || !paused || submitting) return;
    setSubmitting(true);
    setError('');
    try {
      await post(`projects/${project.id}/chat`, {
        message: paused.message,
        baseRevisionId: paused.base_revision_id,
        selectedIds: paused.selected_ids,
        idempotencyKey: crypto.randomUUID(),
      });
      await refresh(project.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <TooltipProvider>
      <main className={`workspace ${!sidebar ? 'sidebar-collapsed' : ''} mobile-${mobilePanel}`}>
        <header className="topbar">
          <Link className="brand" href="/" aria-label="Forma home">
            <span className="forma-symbol">f</span>forma<span className="wordmark-dot">.</span>
          </Link>
          <div className="header-divider" />
          <button className="workspace-toggle" aria-expanded={sidebar} aria-controls="workspace-sidebar" onClick={() => setSidebar((visible) => !visible)}>
            <Layers3 size={16} />
            <span>{sidebar ? 'Hide workspace' : 'Show workspace'}</span>
          </button>
          <button className="project-switch" onClick={() => setModal('projects')}>
            <FolderOpen size={15} />
            <span>{project?.name ?? 'New project'}</span>
            <ChevronDown size={13} />
          </button>
          <span className="private-tag">
            <ShieldCheck size={12} />
            Private workspace
          </span>
          <div className="topbar-spacer" />
          <span className={`save-status ${run ? 'working' : ''}`}>
            <span />
            {run
              ? 'Working on your design'
              : revision
                ? 'All changes saved'
                : configured
                  ? 'Ready when you are'
                  : 'Setup required'}
          </span>
          <button className="subtle-btn history-button" onClick={() => setModal('history')}>
            <History size={15} />
            History
          </button>
          <button
            className="export-btn"
            aria-label="Export STEP"
            disabled={!fileSelection}
            onClick={() => void download(fileSelection)}
          >
            <ArrowDownToLine size={15} />
            <span>Export STEP</span>
          </button>
          <button
            className="avatar"
            aria-label="Open workspace settings"
            onClick={() => setModal('settings')}
          >
            {profile?.display_name.slice(0, 1).toUpperCase() ?? 'S'}
          </button>
        </header>
        <aside className="project-sidebar" id="workspace-sidebar" aria-label="Workspace files and structure">
          <div className="sidebar-heading">
            <span>WORKSPACE</span>
          </div>
          <button className="project-nav active" onClick={() => setModal('projects')}>
            <FolderOpen size={17} />
            <span>{project?.name ?? 'Your next idea'}</span>
            <MoreHorizontal size={16} />
          </button>
          <div className="sidebar-tabs" role="tablist" aria-label="Project content">
            <button role="tab" aria-selected={tab === 'model'} onClick={() => setTab('model')}>
              <Boxes size={14} />
              Model
            </button>
            <button role="tab" aria-selected={tab === 'files'} onClick={() => setTab('files')}>
              <FileBox size={14} />
              Files<span>{currentArtifacts.filter((a) => a.kind === 'step').length || ''}</span>
            </button>
          </div>
          <div className="tree-scroll">
            {tab === 'model' ? (
              <>
                <div className="tree-label">
                  COMPONENTS <span>{selectable.length.toString().padStart(2, '0')}</span>
                </div>
                {selectable.length ? (
                  <div className="component-tree">
                    {selectable.map((item) => {
                      const def = manifest.components.find((c) => c.id === item.definitionId);
                      const depth = (() => {
                        let d = 0;
                        let parent = item.parentId;
                        const seen = new Set<string>();
                        while (parent && !seen.has(parent)) {
                          seen.add(parent);
                          d++;
                          parent = selectable.find((i) => i.id === parent)?.parentId ?? null;
                        }
                        return d;
                      })();
                      return (
                        <div
                          className={`tree-row ${selected === item.id ? 'selected' : ''} ${hiddenInstances.includes(item.id) ? 'hidden-part' : ''}`}
                          key={item.id}
                          style={{ paddingLeft: 12 + depth * 12 }}
                        >
                          <button
                            className="tree-select"
                            onClick={() => {
                              setSelected(item.id);
                              setPreviewOverride(null);
                            }}
                          >
                            <span className="part-color" style={{ background: def?.color }} />
                            {def?.kind === 'assembly' ? <Boxes size={15} /> : <Box size={15} />}
                            <span>{item.name}</span>
                          </button>
                          <IconButton
                            label={`${hidden.includes(item.id) ? 'Show' : 'Hide'} ${item.name}`}
                            onClick={() => toggleHidden(item.id)}
                          >
                            {hidden.includes(item.id) ? <EyeOff size={13} /> : <Eye size={13} />}
                          </IconButton>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="tree-empty">
                    <Layers3 size={24} strokeWidth={1.25} />
                    <p>A place for every part.</p>
                    <span>Your components will appear here as your design takes shape.</span>
                  </div>
                )}
              </>
            ) : (
              <>
                <div className="tree-label">GENERATED FILES</div>
                {currentArtifacts
                  .filter((a) => a.kind === 'step')
                  .map((a) => (
                    <button className="file-row" key={a.id} onClick={() => void download(a)}>
                      <FileBox size={18} />
                      <span>
                        {a.name}
                        <small>{(a.bytes / 1024).toFixed(1)} KB · STEP</small>
                      </span>
                      <ArrowDownToLine size={13} />
                    </button>
                  ))}
                {!currentArtifacts.length && (
                  <div className="tree-empty">
                    <FileBox size={24} strokeWidth={1.25} />
                    <p>Exports, all together.</p>
                    <span>Validated STEP files will be available after your first build.</span>
                  </div>
                )}
              </>
            )}
          </div>
          {selectedItem && (
            <div className="selection-details">
              <span className="eyebrow">SELECTION</span>
              <strong>{selectedItem.name}</strong>
              <span>{selectedDefinition?.kind} · millimetres</span>
              <div>
                <button
                  className="subtle-btn"
                  onClick={() =>
                    setHidden(
                      selectable
                        .filter(
                          (i) =>
                            !selectable.some((child) => child.parentId === i.id) &&
                            !descendants(selected!).includes(i.id),
                        )
                        .map((i) => i.id),
                    )
                  }
                >
                  <Focus size={13} />
                  Isolate
                </button>
                <button
                  className="subtle-btn"
                  onClick={() => {
                    setHidden([]);
                    setSelected(null);
                  }}
                >
                  Clear
                </button>
                <button
                  className="subtle-btn"
                  onClick={() => {
                    const componentPreview = currentArtifacts.find(
                      (a) => a.kind === 'glb' && a.component_id === selectedDefinition?.id,
                    );
                    if (componentPreview) {
                      setPreviewOverride(componentPreview.id);
                      setViewerTab('preview');
                    }
                  }}
                >
                  Preview part
                </button>
              </div>
            </div>
          )}
          <div className="sidebar-bottom">
            <div className="workspace-note">
              <span className="tiny-orbit" />
              <span>
                Room for a new perspective.
                <br />
                <strong>Make something that matters.</strong>
              </span>
            </div>
            <button onClick={() => setModal('help')}>
              <CircleHelp size={15} />A little guidance
              <ArrowUpRight size={13} />
            </button>
            <button onClick={() => setModal('settings')}>
              <Settings2 size={15} />
              Workspace settings
            </button>
          </div>
        </aside>
        <section className="design-panel" aria-label="Design workspace">
          <div className="design-toolbar">
            <div className="document-tabs">
              <button
                className={viewerTab === 'preview' ? 'active' : ''}
                onClick={() => setViewerTab('preview')}
              >
                <Box size={15} />
                3D preview{revision && <span>v{revision.ordinal}</span>}
              </button>
              <button
                className={viewerTab === 'calculations' ? 'active' : ''}
                onClick={() => setViewerTab('calculations')}
              >
                <Calculator size={15} />
                Calculations
                {state?.calculations.length ? <span>{state.calculations.length}</span> : null}
              </button>
            </div>
            <div className="toolbar-right">
              <span className="units-chip">mm</span>
              {previewOverride && (
                <button className="subtle-btn" onClick={() => setPreviewOverride(null)}>
                  Full model
                </button>
              )}
              <IconButton
                label="Fit model to view"
                disabled={!preview}
                onClick={() => setFitVersion((v) => v + 1)}
              >
                <Maximize size={15} />
              </IconButton>
            </div>
          </div>
          {viewerTab === 'preview' ? (
            <div className={`viewport ${grid ? 'with-grid' : ''}`}>
              {loading ? (
                <div className="viewer-loading">
                  <LoaderCircle className="spin" size={20} />
                  Opening project…
                </div>
              ) : preview ? (
                <CadViewer
                  key={preview.id}
                  artifactId={preview.id}
                  selected={
                    previewOverride
                      ? selectedDefinition
                        ? [selectedDefinition.id]
                        : []
                      : selected
                        ? descendants(selected)
                        : []
                  }
                  hidden={previewOverride ? [] : hiddenInstances}
                  onSelect={previewOverride ? () => {} : setSelected}
                  grid={grid}
                  wireframe={wireframe}
                  fitVersion={fitVersion}
                />
              ) : (
                <div className="empty-viewport">
                  <div className="orbit-art">
                    <div className="orbit-ring one" />
                    <div className="orbit-ring two" />
                    <div className="orbit-core">
                      <Box size={64} strokeWidth={0.8} />
                    </div>
                    <span className="orbit-point" />
                  </div>
                  <span className="eyebrow">AN OPEN SPACE FOR YOUR IDEAS</span>
                  <h1>
                    From a thought
                    <br />
                    to a thing.
                  </h1>
                  <p>
                    Describe what you have in mind.
                    <br />
                    We’ll give it a little dimension.
                  </p>
                  <button
                    className="start-design-btn"
                    onClick={() => {
                      setMobilePanel('chat');
                      textarea.current?.focus();
                    }}
                  >
                    Start a conversation <ArrowUpRight size={14} />
                  </button>
                </div>
              )}
              <div className="view-label">
                <CircleDot size={11} />
                {preview ? 'PERSPECTIVE' : 'YOUR CANVAS'}
                <span>·</span>
                {selectedItem?.name ?? 'Nothing selected'}
              </div>
              <div className="floating-tools">
                <IconButton
                  label="Select components"
                  active={!wireframe}
                  onClick={() => setWireframe(false)}
                >
                  <MousePointer2 size={17} />
                </IconButton>
                <span />
                <IconButton
                  label="Toggle wireframe"
                  active={wireframe}
                  onClick={() => setWireframe(!wireframe)}
                >
                  <Box size={17} />
                </IconButton>
                <IconButton label="Toggle grid" active={grid} onClick={() => setGrid(!grid)}>
                  <Grid2X2 size={17} />
                </IconButton>
                <span />
                <IconButton
                  label="Reset view"
                  onClick={() => {
                    setFitVersion((v) => v + 1);
                    setHidden([]);
                    setSelected(null);
                  }}
                >
                  <RotateCcw size={16} />
                </IconButton>
              </div>
              {!preview && (
                <div className="axis-key">
                  <span className="axis-z">z</span>
                  <span className="axis-y">y</span>
                  <span className="axis-x">x</span>
                  <i />
                </div>
              )}
              <div className="viewport-footer">
                <span>
                  <Move3D size={12} />
                  Orbit · drag&nbsp;&nbsp; Pan · right drag&nbsp;&nbsp; Zoom · scroll
                </span>
                <span>
                  {manifest.components.length} components <span className="footer-dot">·</span>{' '}
                  {revision ? 'Validated geometry' : 'No geometry yet'}
                </span>
              </div>
            </div>
          ) : (
            <div className="calculations-view">
              <div className="section-intro">
                <span className="eyebrow">ENGINEERING NOTEBOOK</span>
                <h2>Give your design a reason.</h2>
                <p>Executed calculations, explicit assumptions, and checks you can inspect.</p>
              </div>
              {state?.calculations.length ? (
                state.calculations.map((c) => (
                  <article className="calculation-card" key={c.id}>
                    <div>
                      <Calculator size={18} />
                      <h3>{c.result.title}</h3>
                      <span className={`status-chip ${c.stale ? 'warning' : ''}`}>
                        {c.stale
                          ? 'Needs recalculation'
                          : c.reproducible
                            ? 'Reproduced'
                            : 'Unverified'}
                      </span>
                    </div>
                    <dl>
                      {Object.entries(c.result.results).map(([key, value]) => (
                        <div key={key}>
                          <dt>{key}</dt>
                          <dd>
                            {value.value.toLocaleString(undefined, { maximumSignificantDigits: 6 })}
                            <small>{value.unit}</small>
                          </dd>
                        </div>
                      ))}
                    </dl>
                    <MessageResponse>{c.result.conclusion}</MessageResponse>
                    <details>
                      <summary>Inputs, assumptions & checks</summary>
                      <ul>
                        {c.result.assumptions.map((a) => (
                          <li key={a}>{a}</li>
                        ))}
                      </ul>
                      {c.result.checks.map((check) => (
                        <p key={check.name}>
                          {check.passed ? '✓' : '!'} {check.name}: {check.detail}
                        </p>
                      ))}
                    </details>
                    <small>
                      Execution is evidence of reproducibility, not certification of engineering
                      suitability.
                    </small>
                  </article>
                ))
              ) : (
                <div className="calculation-empty">
                  <Calculator size={32} strokeWidth={1} />
                  <h3>Reason. Calculate. Refine.</h3>
                  <p>
                    Ask the engineering agent to run a calculation.
                    <br />
                    Its results and assumptions will live here.
                  </p>
                  <button
                    className="subtle-btn"
                    onClick={() =>
                      suggest(
                        'Help me calculate the deflection of a beam. Ask me for the dimensions, material, supports, and loads you need.',
                      )
                    }
                  >
                    <Plus size={14} />
                    Start a calculation
                  </button>
                </div>
              )}
            </div>
          )}
        </section>
        <section className="chat-panel" aria-label="Design conversation">
          <div className="chat-heading">
            <span className="agent-icon">
              <Sparkles size={16} />
            </span>
            <div>
              <strong>Design partner</strong>
              <span>Let’s think it through.</span>
            </div>
            <span className="agent-tag">FORMA</span>
            <IconButton label="New project" onClick={() => setModal('projects')}>
              <Plus size={17} />
            </IconButton>
          </div>
          <Conversation
            key={`${project?.id ?? 'empty'}-${Boolean(state?.messages.length)}`}
            className="chat-conversation"
            initial={state?.messages.length ? 'instant' : false}
            resize="smooth"
          >
            <ConversationContent className="chat-content">
              {!state?.messages.length ? (
                <div className="chat-welcome">
                  <div className="welcome-spark">
                    <Sparkles size={21} strokeWidth={1.3} />
                  </div>
                  <h2>
                    What are we
                    <br />
                    making today?
                  </h2>
                  <p>
                    A single part. A moving assembly.
                    <br />
                    Or an idea you haven’t quite figured out.
                  </p>
                  <div className="suggestions">
                    <button
                      onClick={() =>
                        suggest(
                          'Create a parametric mounting bracket. Ask me which dimensions and mounting pattern I need.',
                        )
                      }
                    >
                      <Box size={17} />
                      <span>
                        Design a component<small>Start with a shape and a purpose</small>
                      </span>
                      <ArrowUpRight size={14} />
                    </button>
                    <button
                      onClick={() =>
                        suggest(
                          'Help me design an assembly with reusable components. Let’s define its mechanism and dimensions first.',
                        )
                      }
                    >
                      <Boxes size={17} />
                      <span>
                        Bring parts together<small>Build an assembly, piece by piece</small>
                      </span>
                      <ArrowUpRight size={14} />
                    </button>
                    <button
                      onClick={() => {
                        setViewerTab('calculations');
                        suggest(
                          'I have an engineering calculation to solve. Help me define the inputs and verify the result.',
                        );
                      }}
                    >
                      <Calculator size={17} />
                      <span>
                        Work through the numbers<small>Calculate, check, and understand</small>
                      </span>
                      <ArrowUpRight size={14} />
                    </button>
                  </div>
                  <div className="chat-intro-note">
                    <ShieldCheck size={13} />
                    Your ideas stay in your private workspace.
                  </div>
                </div>
              ) : (
                state.messages.map((message) => (
                  <div key={message.id} className={`chat-message ${message.role}`}>
                    <div className="message-author">
                      {message.role === 'assistant' ? (
                        <Sparkles size={13} />
                      ) : (
                        <span className="user-dot" />
                      )}
                      {message.role === 'assistant' ? 'Forma' : 'You'}
                      <time>
                        {new Date(message.created_at).toLocaleTimeString([], {
                          hour: '2-digit',
                          minute: '2-digit',
                        })}
                      </time>
                    </div>
                    {message.role === 'assistant' ? (
                      <MessageResponse>{message.content}</MessageResponse>
                    ) : (
                      <p>{message.content}</p>
                    )}
                  </div>
                ))
              )}
              {(run?.status === 'queued' || run?.status === 'running' || submitting) && (
                <div className="run-progress" role="status">
                  <LoaderCircle className="spin" size={15} />
                  <span>
                    {state?.events.filter((e) => e.run_id === run?.id).at(-1)?.message ??
                      'Preparing your request…'}
                  </span>
                </div>
              )}
              {waitingInput && (
                <div className="run-waiting" role="status">
                  <CircleHelp size={15} />
                  <span>
                    Forma is waiting for your input. Reply to the question above,
                    or type <strong>approve</strong> to start the CAD build.
                  </span>
                </div>
              )}
              {state?.events.length ? (
                <details className="activity">
                  <summary>
                    <Layers3 size={12} />
                    Run activity
                    <ChevronDown size={12} />
                  </summary>
                  {state.events.slice(-12).map((e) => (
                    <div key={e.id}>
                      <span className={`activity-dot ${e.kind}`} />
                      {e.message}
                    </div>
                  ))}
                </details>
              ) : null}
              {revision?.validation && (
                <details className="activity requirement-checks">
                  <summary><ShieldCheck size={14} /> Automated evidence</summary>
                  {revision.validation.requirements.map((check) => (
                    <p key={check.id}><strong>{check.status === 'passed' ? '✓ Verified' : check.status === 'failed' ? 'Failed' : 'Unverified'}</strong> · {check.description}</p>
                  ))}
                  {!revision.validation.allRequirementsVerified && <p>These measurements are advisory. Review the draft before use; unsupported requirements were not checked automatically.</p>}
                </details>
              )}
            </ConversationContent>
            {!!state?.messages.length && <ConversationScrollButton />}
          </Conversation>
          <div className="composer-area">
            {!configured && (
              <div className="setup-notice">
                <span className="setup-dot" />
                <span>
                  Your workspace is taking shape.
                  <br />
                  <strong>Connect services to start designing.</strong>
                </span>
                <button onClick={() => setModal('settings')} aria-label="View setup requirements">
                  <ArrowUpRight size={15} />
                </button>
              </div>
            )}
            {error && (
              <div className="error-notice" role="alert">
                {error}
                <button aria-label="Dismiss error" onClick={() => setError('')}>
                  <X size={13} />
                </button>
              </div>
            )}
            {paused && (
              pausedRequestUncertain ? (
                <div className="resume-stack">
                  <p className="resume-hint">The previous model request timed out. Start a fresh run so Forma does not repeat an uncertain billable request.</p>
                  <button className="resume-btn" onClick={() => void restartPaused()} disabled={submitting}>
                    <RotateCcw size={14} />
                    Start fresh run
                  </button>
                </div>
              ) : (
                <button className="resume-btn" onClick={() => void runAction('continue', paused.id)}>
                  <RotateCcw size={14} />
                  Continue saved work
                </button>
              )
            )}
            <form
              className="composer"
              onSubmit={(e) => {
                e.preventDefault();
                void submit();
              }}
            >
              {selectedItem && (
                <div className="selection-chip">
                  <Box size={12} />
                  {selectedItem.name}
                  <button
                    aria-label="Clear chat selection"
                    type="button"
                    onClick={() => setSelected(null)}
                  >
                    <X size={12} />
                  </button>
                </div>
              )}
              <textarea
                ref={textarea}
                aria-label="Message your design partner"
                placeholder="Describe your idea, ask a question…"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    void submit();
                  }
                }}
                maxLength={12000}
                rows={3}
              />
              <div className="composer-bottom">
                <span>
                  <Sparkles size={13} />
                  CAD + engineering
                </span>
                {run && run.status !== 'waiting_input' ? (
                  <button
                    className="send-btn"
                    type="button"
                    aria-label="Stop generation"
                    onClick={() => void runAction('cancel', run.id)}
                  >
                    <Square size={13} fill="currentColor" />
                  </button>
                ) : (
                  <button
                    className="send-btn"
                    aria-label="Send message"
                    disabled={!configured || !draft.trim() || submitting}
                  >
                    {submitting ? (
                      <LoaderCircle className="spin" size={16} />
                    ) : (
                      <ArrowUp size={17} />
                    )}
                  </button>
                )}
              </div>
            </form>
            <div className="composer-footnote">
              <span>Thoughtful design starts with a conversation.</span>
              <span>
                <Command size={10} />K
              </span>
            </div>
          </div>
        </section>
        <div className="mobile-switch">
          <button
            className={mobilePanel === 'preview' ? 'active' : ''}
            onClick={() => setMobilePanel('preview')}
          >
            <Box size={15} />
            Workspace
          </button>
          <button
            className={mobilePanel === 'chat' ? 'active' : ''}
            onClick={() => setMobilePanel('chat')}
          >
            <MessageSquare size={15} />
            Conversation
          </button>
        </div>
        <Dialog
          open={modal !== null}
          onOpenChange={(open) => {
            if (!open) {
              setModal(null);
              setNotice('');
            }
          }}
        >
          <DialogContent className={modal === 'settings' ? 'settings-dialog' : 'workspace-dialog'}>
            <DialogHeader>
              <DialogTitle>
                {
                  {
                    projects: 'Your projects',
                    history: 'Design history',
                    settings: 'Workspace settings',
                    feedback: 'A note from the workbench',
                    help: 'A little guidance',
                  }[modal ?? 'help']
                }
              </DialogTitle>
              <DialogDescription>
                {
                  {
                    projects: 'A separate space for each idea.',
                    history: 'Every successful design has a place in your history.',
                    settings: 'Configure the tools behind your workspace.',
                    feedback: 'Your feedback stays attached to this design revision.',
                    help: 'You describe the intent. Forma handles the workspace.',
                  }[modal ?? 'help']
                }
              </DialogDescription>
            </DialogHeader>
            {modal === 'projects' && (
              <div className="project-dialog-body">
                <div className="search-field">
                  <Search size={15} />
                  <input
                    aria-label="Search projects"
                    placeholder="Find a project…"
                    value={projectQuery}
                    onChange={(e) => setProjectQuery(e.target.value)}
                  />
                </div>
                {projects
                  .filter((p) => p.name.toLowerCase().includes(projectQuery.toLowerCase()))
                  .map((p) => (
                    <button
                      className="project-list-row"
                      key={p.id}
                      onClick={() => void openProject(p.id)}
                    >
                      <FolderOpen size={18} />
                      <span>
                        {p.name}
                        <small>Updated {new Date(p.updated_at).toLocaleDateString()}</small>
                      </span>
                      {p.id === project?.id ? <Check size={15} /> : <ChevronRight size={15} />}
                    </button>
                  ))}
                {!projects.length && <p className="muted-copy">Your projects will appear here.</p>}
                <form
                  className="new-project-form"
                  onSubmit={async (e) => {
                    e.preventDefault();
                    setError('');
                    try {
                      const next = await post<Project>('projects', { name: newName });
                      setProjects((p) => [next, ...p]);
                      setNewName('');
                      await openProject(next.id);
                    } catch (err) {
                      setNotice((err as Error).message);
                    }
                  }}
                >
                  <input
                    aria-label="New project name"
                    placeholder="Name your next project"
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    maxLength={100}
                    required
                  />
                  <button className="primary-btn" disabled={!configured || !newName.trim()}>
                    <Plus size={15} />
                    Create project
                  </button>
                </form>
              </div>
            )}
            {modal === 'history' && (
              <div className="history-list">
                {state?.revisions.length ? (
                  state.revisions.map((r) => (
                    <article key={r.id}>
                      <span className="revision-number">
                        {r.ordinal.toString().padStart(2, '0')}
                      </span>
                      <div>
                        <strong>{r.summary}</strong>
                        <small>
                          {new Date(r.created_at).toLocaleString()}{' '}
                          {r.id === project?.current_revision_id ? '· Current revision' : ''}
                        </small>
                      </div>
                      <button
                        className="subtle-btn"
                        disabled={r.id === project?.current_revision_id || !!run}
                        onClick={() => {
                          setModal(null);
                          suggest(
                            `Restore revision ${r.id} (version ${r.ordinal}). Rebuild and validate it, then publish it as a new revision.`,
                          );
                        }}
                      >
                        Restore in chat
                      </button>
                    </article>
                  ))
                ) : (
                  <div className="dialog-empty">
                    <History size={28} />
                    <p>Your first successful build will begin the story.</p>
                  </div>
                )}
              </div>
            )}
            {modal === 'settings' &&
              (profile?.role === 'admin' ? (
                <AdminPanel />
              ) : (
                <div className="setup-checklist">
                  <div className="setup-title">
                    <ShieldCheck size={24} />
                    <h3>{configured ? 'Your private workspace' : 'Ready for its foundations.'}</h3>
                  </div>
                  <p>
                    {configured
                      ? 'Your administrator manages model access and execution limits.'
                      : 'The interface is ready to explore. Live design generation needs these services configured on the server.'}
                  </p>
                  {!configured &&
                    [
                      'Supabase project, private storage & migrations',
                      'Administrator account & model encryption key',
                      'OpenRouter model and API key',
                      'Vercel CAD runtime snapshot',
                    ].map((text, i) => (
                      <div className="setup-check" key={text}>
                        <span>{i + 1}</span>
                        {text}
                      </div>
                    ))}
                  {configured && (
                    <button
                      className="subtle-btn"
                      onClick={async () => {
                        await post('auth/logout');
                        window.location.reload();
                      }}
                    >
                      Sign out
                    </button>
                  )}
                  <span className="muted-copy">
                    No keys are stored in the browser. No paid upgrades are automatic.
                  </span>
                </div>
              ))}
            {modal === 'feedback' && (
              <form
                className="feedback-form"
                onSubmit={async (e) => {
                  e.preventDefault();
                  if (!project) return;
                  try {
                    await post(`projects/${project.id}/feedback`, {
                      content: feedback,
                      runId: state?.runs[0]?.id ?? null,
                    });
                    setNotice('Thank you. Your feedback is attached to this design.');
                    setFeedback('');
                  } catch (err) {
                    setNotice((err as Error).message);
                  }
                }}
              >
                <textarea
                  aria-label="Design feedback"
                  placeholder="What worked? What felt difficult?"
                  value={feedback}
                  onChange={(e) => setFeedback(e.target.value)}
                  rows={5}
                  maxLength={4000}
                  required
                />
                <button className="primary-btn" disabled={!project || !feedback.trim()}>
                  Save feedback
                </button>
              </form>
            )}
            {modal === 'help' && (
              <div className="help-content">
                <p>
                  <strong>Start with purpose.</strong> Describe what you are making, key dimensions,
                  and any constraints.
                </p>
                <p>
                  <strong>Talk to a part.</strong> Select a component in the tree or preview, then
                  ask for a change in chat.
                </p>
                <p>
                  <strong>Keep the good versions.</strong> Successful builds create revisions.
                  Failed attempts leave the previous design intact.
                </p>
                <p>
                  <strong>Check the numbers.</strong> Ask the engineering agent to run calculations.
                  Provide loads, materials, and assumptions.
                </p>
                <button
                  className="subtle-btn"
                  disabled={!project}
                  onClick={() => setModal('feedback')}
                >
                  <MessageSquare size={15} />
                  Leave feedback on this design
                </button>
              </div>
            )}
            {notice && (
              <p className="notice" role="status">
                {notice}
              </p>
            )}
          </DialogContent>
        </Dialog>
      </main>
    </TooltipProvider>
  );
}
