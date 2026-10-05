'use client';
import { useEffect, useRef, useState } from 'react';
import {
  FilePenLine,
  Plus,
  Ruler,
  Download,
  ZoomIn,
  ZoomOut,
  LoaderCircle,
  AlertTriangle,
  Trash2,
} from 'lucide-react';
import type {
  Artifact,
  DrawingSheetSpec,
  DrawingReference,
  DimensionSpec,
  FeatureControlFrame,
  ProjectManifest,
} from '@forma/core';

type Feature = DrawingReference & {
  id: string;
  radiusMm?: number;
  referenceStatus?: 'unique' | 'ambiguous';
};
type Primitive = {
  type: string;
  x?: number;
  y?: number;
  x1?: number;
  y1?: number;
  x2?: number;
  y2?: number;
  width?: number;
  height?: number;
  cx?: number;
  cy?: number;
  radius?: number;
  points?: number[][];
  dash?: boolean;
  text?: string;
  fontSize?: number;
  anchor?: 'start' | 'middle' | 'end';
};
type Sheet = {
  id: string;
  title: string;
  componentId: string;
  status: string;
  widthMm: number;
  heightMm: number;
  scale: number;
  primitives: Primitive[];
  features: Feature[];
  dimensions: Array<DimensionSpec & { value: number; text: string }>;
  issues: Array<{ id: string; message: string }>;
};
export type DrawingDocument = { sheets: Sheet[]; status: string };

function newSheet(componentId: string, title: string): DrawingSheetSpec {
  return {
    id: `sheet_${crypto.randomUUID().slice(0, 8)}`,
    componentId,
    title,
    paper: 'A3',
    projection: 'third_angle',
    standard: 'ISO',
    scale: null,
    views: ['front', 'top', 'right', 'isometric'].map((kind) => ({
      id: kind,
      kind: kind as 'front' | 'top' | 'right' | 'isometric',
      sectionAxis: 'Y',
      sectionOffsetMm: 0,
      hiddenLines: true,
    })),
    autoDimensions: true,
    dimensions: [],
    datums: [],
    controls: [],
    notes: [],
    includeBom: false,
  };
}

function reference(feature: Feature): DrawingReference {
  return {
    kind: feature.kind,
    origin: feature.origin,
    direction: feature.direction,
    toleranceMm: 0.001,
  };
}

function DrawingCanvas({ sheet, zoom }: { sheet: Sheet; zoom: number }) {
  return (
    <svg
      className="drawing-paper"
      style={{ width: `${zoom * 100}%` }}
      viewBox={`0 0 ${sheet.widthMm} ${sheet.heightMm}`}
      role="img"
      aria-label={`${sheet.title}, engineering drawing draft`}
    >
      <rect width={sheet.widthMm} height={sheet.heightMm} fill="white" />
      <g stroke="#26382f" strokeWidth={0.25} fill="none">
        {sheet.primitives.map((p, i) => {
          const dash = p.dash ? '2 1' : undefined;
          switch (p.type) {
            case 'line':
              return (
                <line key={i} x1={p.x1} y1={p.y1} x2={p.x2} y2={p.y2} strokeDasharray={dash} />
              );
            case 'rect':
              return <rect key={i} x={p.x} y={p.y} width={p.width} height={p.height} />;
            case 'circle':
              return <circle key={i} cx={p.cx} cy={p.cy} r={p.radius} strokeDasharray={dash} />;
            case 'polyline':
              return (
                <polyline
                  key={i}
                  points={p.points?.map(([x, y]) => `${x},${y}`).join(' ')}
                  strokeDasharray={dash}
                />
              );
            case 'text':
              return (
                <text
                  key={i}
                  x={p.x}
                  y={p.y}
                  fontSize={p.fontSize}
                  textAnchor={p.anchor}
                  fontFamily="DejaVu Sans, sans-serif"
                  stroke="none"
                  fill="#26382f"
                >
                  {p.text}
                </text>
              );
            default:
              return null;
          }
        })}
      </g>
    </svg>
  );
}

export function DrawingPanel({
  manifest,
  document,
  revisionId,
  busy,
  artifacts,
  onGenerate,
  onDownload,
  onDirtyChange,
}: {
  manifest: ProjectManifest;
  document?: DrawingDocument;
  revisionId?: string;
  busy: boolean;
  artifacts: Artifact[];
  onGenerate: (sheets: DrawingSheetSpec[], key: string) => Promise<void>;
  onDownload: (artifact: Artifact) => Promise<void>;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const initial = manifest.drawings[0];
  const [draft, setDraft] = useState<DrawingSheetSpec | null>(initial ?? null);
  const [drafts, setDrafts] = useState<Record<string, DrawingSheetSpec>>(() =>
    Object.fromEntries(manifest.drawings.map((sheet) => [sheet.id, sheet])),
  );
  const [target, setTarget] = useState(
    initial?.componentId ?? manifest.rootComponentId ?? manifest.components[0]?.id ?? '',
  );
  const [zoom, setZoom] = useState(1);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [featureId, setFeatureId] = useState('');
  const [secondFeatureId, setSecondFeatureId] = useState('');
  const [viewId, setViewId] = useState('top');
  const [datumLabel, setDatumLabel] = useState('A');
  const [characteristic, setCharacteristic] =
    useState<FeatureControlFrame['characteristic']>('position');
  const [tolerance, setTolerance] = useState('0.1');
  const [datumSequence, setDatumSequence] = useState('');
  const [dimensionKind, setDimensionKind] = useState<DimensionSpec['kind']>('extent');
  const [axis, setAxis] = useState<'X' | 'Y' | 'Z'>('X');
  const [upper, setUpper] = useState('');
  const [lower, setLower] = useState('');
  const [basic, setBasic] = useState(false);
  const [offset, setOffset] = useState(10);
  const [materialCondition, setMaterialCondition] =
    useState<FeatureControlFrame['materialCondition']>('none');
  const request = useRef<{ payload: string; key: string } | null>(null);
  const savedDrafts = Object.fromEntries(manifest.drawings.map((sheet) => [sheet.id, sheet]));
  const dirty = JSON.stringify(drafts) !== JSON.stringify(savedDrafts);
  const sheetDirty = Boolean(
    draft && JSON.stringify(draft) !== JSON.stringify(savedDrafts[draft.id]),
  );
  const sheet = document?.sheets.find((s) => s.id === draft?.id);
  const features = sheet?.features ?? [];
  const feature = features.find((f) => f.id === featureId);
  const secondFeature = features.find((f) => f.id === secondFeatureId);
  const annotationViewId = draft?.views.some((v) => v.id === viewId && v.kind !== 'isometric')
    ? viewId
    : (draft?.views.find((v) => v.kind !== 'isometric')?.id ?? '');
  const canGenerate = Boolean(revisionId && target && !busy && !saving);
  useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange]);
  function update(change: Partial<DrawingSheetSpec>) {
    if (draft) {
      const next = { ...draft, ...change };
      setDraft(next);
      setDrafts((previous) => ({ ...previous, [next.id]: next }));
    }
  }
  async function generate() {
    const component = manifest.components.find((c) => c.id === target);
    if (!component || !canGenerate) return;
    const spec = draft ?? newSheet(target, component.name);
    const sheets = Object.values({ ...drafts, [spec.id]: spec });
    const payload = JSON.stringify({ revisionId, sheets });
    if (request.current?.payload !== payload)
      request.current = { payload, key: crypto.randomUUID() };
    setError('');
    setSaving(true);
    setDraft(spec);
    setDrafts((previous) => ({ ...previous, [spec.id]: spec }));
    try {
      await onGenerate(sheets, request.current.key);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Drawing generation could not start.');
    } finally {
      setSaving(false);
    }
  }
  function addDimension() {
    if (!draft) return;
    if (!annotationViewId) {
      setError('Select an orthographic or section view.');
      return;
    }
    if (
      ['diameter', 'radius'].includes(dimensionKind) &&
      (!feature || feature.kind !== 'cylinder')
    ) {
      setError('Select a cylindrical feature for diameter or radius.');
      return;
    }
    if (
      ['distance', 'angle'].includes(dimensionKind) &&
      (!feature || !secondFeature || feature.id === secondFeature.id)
    ) {
      setError('Select two different measured features.');
      return;
    }
    if (
      dimensionKind === 'angle' &&
      (feature?.kind !== 'plane' || secondFeature?.kind !== 'plane')
    ) {
      setError('An angular dimension needs two planar features viewed edge-on.');
      return;
    }
    if (basic && (upper !== '' || lower !== '')) {
      setError('Basic dimensions cannot have size tolerances.');
      return;
    }
    if (
      (upper === '') !== (lower === '') ||
      (upper !== '' &&
        (!Number.isFinite(Number(upper)) ||
          !Number.isFinite(Number(lower)) ||
          Number(upper) < 0 ||
          Number(lower) < 0))
    ) {
      setError('Provide both positive upper and lower tolerance magnitudes, or leave both empty.');
      return;
    }
    update({
      dimensions: [
        ...draft.dimensions,
        {
          id: `dim_${crypto.randomUUID().slice(0, 8)}`,
          viewId: annotationViewId,
          kind: dimensionKind,
          axis,
          reference: feature && dimensionKind !== 'extent' ? reference(feature) : null,
          secondReference:
            secondFeature && ['distance', 'angle'].includes(dimensionKind)
              ? reference(secondFeature)
              : null,
          upperTolerance: upper === '' ? null : Number(upper),
          lowerTolerance: lower === '' ? null : Number(lower),
          basic,
          offsetMm: offset,
        },
      ],
    });
    setError('');
  }
  function addDatum() {
    if (!draft || !feature) {
      setError('Select the feature that defines this datum.');
      return;
    }
    if (!/^[A-Z]{1,2}$/.test(datumLabel) || draft.datums.some((d) => d.label === datumLabel)) {
      setError('Use a unique datum label, such as A or B.');
      return;
    }
    update({
      datums: [
        ...draft.datums,
        {
          label: datumLabel,
          viewId: annotationViewId,
          reference: reference(feature),
          offset: [-14, 12],
        },
      ],
    });
    setError('');
  }
  function addControl() {
    if (!draft || !feature || !Number.isFinite(Number(tolerance)) || Number(tolerance) <= 0) {
      setError('Select a feature and enter a positive tolerance in millimetres.');
      return;
    }
    const datums = datumSequence
      .split(',')
      .map((d) => d.trim())
      .filter(Boolean);
    const form = ['flatness', 'straightness', 'circularity', 'cylindricity'].includes(
      characteristic,
    );
    if (
      (form && datums.length) ||
      ([
        'position',
        'parallelism',
        'perpendicularity',
        'angularity',
        'circular_runout',
        'total_runout',
      ].includes(characteristic) &&
        !datums.length)
    ) {
      setError(form ? 'Form controls do not use datums.' : 'This control needs a datum sequence.');
      return;
    }
    if (
      new Set(datums).size !== datums.length ||
      datums.some((label) => !draft.datums.some((d) => d.label === label))
    ) {
      setError('Use a sequence of unique, defined datums.');
      return;
    }
    if (
      materialCondition !== 'none' &&
      !(feature.kind === 'cylinder' && characteristic === 'position')
    ) {
      setError('Material modifiers are supported for cylindrical position controls.');
      return;
    }
    update({
      controls: [
        ...draft.controls,
        {
          id: `control_${crypto.randomUUID().slice(0, 8)}`,
          viewId: annotationViewId,
          characteristic,
          reference: reference(feature),
          toleranceMm: Number(tolerance),
          datums,
          zone:
            feature.kind === 'cylinder' && characteristic === 'position' ? 'diameter' : 'linear',
          materialCondition,
          offset: [12, -12],
        },
      ],
    });
    setError('');
  }
  return (
    <div className="drawing-workspace">
      <header className="drawing-heading">
        <div>
          <span className="eyebrow">ENGINEERING DOCUMENTS</span>
          <h2>From model to drawing.</h2>
          <p>Measured views, dimensions and design intent, tied to this revision.</p>
        </div>
        <button className="drawing-primary" disabled={!canGenerate} onClick={() => void generate()}>
          {saving || busy ? <LoaderCircle size={15} className="spin" /> : <FilePenLine size={15} />}
          {draft ? 'Regenerate sheet' : 'Generate drawing'}
        </button>
      </header>
      {error ? (
        <p className="drawing-error" role="alert">
          {error}
        </p>
      ) : null}
      {!revisionId ? (
        <div className="drawing-empty">
          <Ruler size={36} />
          <h3>A drawing starts with a validated model.</h3>
          <p>Build a part or assembly first, then create its engineering sheets here.</p>
        </div>
      ) : (
        <>
          <div className="drawing-sheetbar">
            <label>
              Drawing sheet
              <select
                value={draft?.id ?? ''}
                onChange={(e) => {
                  const selected = drafts[e.target.value] ?? null;
                  setDraft(selected);
                  if (selected) setTarget(selected.componentId);
                  setFeatureId('');
                  setSecondFeatureId('');
                }}
              >
                <option value="">New sheet</option>
                {Object.values(drafts).map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.title}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Part or assembly
              <select
                value={target}
                disabled={Boolean(draft)}
                onChange={(e) => setTarget(e.target.value)}
              >
                {manifest.components.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="drawing-small"
              onClick={() => {
                setDraft(null);
                setFeatureId('');
                setSecondFeatureId('');
              }}
            >
              <Plus size={14} />
              New sheet
            </button>
          </div>
          <div className="drawing-layout">
            <div className="drawing-preview-area">
              <div className="drawing-preview-toolbar">
                <span>
                  {sheet
                    ? sheetDirty
                      ? 'Saved preview · regenerate to apply edits'
                      : `Draft · ${sheet.scale}:1 · mm`
                    : 'Generate the sheet to measure its geometry'}
                </span>
                <div>
                  <button
                    aria-label="Zoom drawing out"
                    onClick={() => setZoom(Math.max(0.5, zoom - 0.25))}
                  >
                    <ZoomOut size={16} />
                  </button>
                  <span>{Math.round(zoom * 100)}%</span>
                  <button
                    aria-label="Zoom drawing in"
                    onClick={() => setZoom(Math.min(3, zoom + 0.25))}
                  >
                    <ZoomIn size={16} />
                  </button>
                </div>
              </div>
              <div className="drawing-canvas-scroll">
                {sheet?.primitives ? (
                  <DrawingCanvas sheet={sheet} zoom={zoom} />
                ) : (
                  <div className="drawing-empty">
                    <FilePenLine size={40} />
                    <p>Front, top, side and isometric views will appear here.</p>
                  </div>
                )}
              </div>
              {sheet?.issues.length ? (
                <div className="drawing-issues" role="status">
                  <strong>
                    <AlertTriangle size={15} /> Resolve these before exporting
                  </strong>
                  {sheet.issues.map((i) => (
                    <p key={`${i.id}:${i.message}`}>
                      {i.id}: {i.message}
                    </p>
                  ))}
                </div>
              ) : null}
              <div className="drawing-downloads">
                {artifacts
                  .filter((a) => a.name.startsWith(`drawing-${draft?.id}.`))
                  .map((a) => (
                    <button
                      key={a.id}
                      disabled={sheetDirty}
                      title={sheetDirty ? 'Regenerate the sheet to export these edits.' : undefined}
                      onClick={() => void onDownload(a)}
                    >
                      <Download size={14} />
                      {a.name.split('.').at(-1)?.toUpperCase()}
                    </button>
                  ))}
              </div>
            </div>
            {draft ? (
              <aside className="drawing-inspector" aria-label="Drawing settings">
                <label>
                  Sheet title
                  <input value={draft.title} onChange={(e) => update({ title: e.target.value })} />
                </label>
                <div className="drawing-input-row">
                  <label>
                    Paper
                    <select
                      value={draft.paper}
                      onChange={(e) => update({ paper: e.target.value as 'A4' | 'A3' })}
                    >
                      <option>A3</option>
                      <option>A4</option>
                    </select>
                  </label>
                  <label>
                    Scale
                    <input
                      type="number"
                      min="0.001"
                      step="0.1"
                      placeholder="Fit automatically"
                      value={draft.scale ?? ''}
                      onChange={(e) =>
                        update({ scale: e.target.value ? Number(e.target.value) : null })
                      }
                    />
                  </label>
                </div>
                <label>
                  Projection
                  <select
                    value={draft.projection}
                    onChange={(e) =>
                      update({ projection: e.target.value as DrawingSheetSpec['projection'] })
                    }
                  >
                    <option value="third_angle">Third angle</option>
                    <option value="first_angle">First angle</option>
                  </select>
                </label>
                <label>
                  Annotation convention
                  <select
                    value={draft.standard}
                    onChange={(e) => update({ standard: e.target.value as 'ISO' | 'ASME' })}
                  >
                    <option>ISO</option>
                    <option>ASME</option>
                  </select>
                </label>
                <label className="drawing-check">
                  <input
                    type="checkbox"
                    checked={draft.autoDimensions}
                    onChange={(e) => update({ autoDimensions: e.target.checked })}
                  />
                  Suggest measured dimensions
                </label>
                <label className="drawing-check">
                  <input
                    type="checkbox"
                    checked={draft.includeBom}
                    onChange={(e) => update({ includeBom: e.target.checked })}
                  />
                  Include assembly parts list
                </label>
                <details>
                  <summary>Section views</summary>
                  {draft.views
                    .filter((v) => v.kind === 'section')
                    .map((v) => (
                      <div key={v.id} className="drawing-input-row">
                        <label>
                          Cut axis
                          <select
                            value={v.sectionAxis}
                            onChange={(e) =>
                              update({
                                views: draft.views.map((view) =>
                                  view.id === v.id
                                    ? { ...view, sectionAxis: e.target.value as 'X' | 'Y' | 'Z' }
                                    : view,
                                ),
                              })
                            }
                          >
                            <option>X</option>
                            <option>Y</option>
                            <option>Z</option>
                          </select>
                        </label>
                        <label>
                          Offset, mm
                          <input
                            type="number"
                            value={v.sectionOffsetMm}
                            onChange={(e) =>
                              update({
                                views: draft.views.map((view) =>
                                  view.id === v.id
                                    ? { ...view, sectionOffsetMm: Number(e.target.value) }
                                    : view,
                                ),
                              })
                            }
                          />
                        </label>
                        <button
                          aria-label="Remove section"
                          onClick={() =>
                            update({ views: draft.views.filter((view) => view.id !== v.id) })
                          }
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    ))}
                  <button
                    className="drawing-small"
                    disabled={draft.views.length >= 8}
                    onClick={() =>
                      update({
                        views: [
                          ...draft.views,
                          {
                            id: `section_${draft.views.length}`,
                            kind: 'section',
                            sectionAxis: 'Y',
                            sectionOffsetMm: 0,
                            hiddenLines: false,
                          },
                        ],
                      })
                    }
                  >
                    <Plus size={13} />
                    Add section
                  </button>
                </details>
                <details open>
                  <summary>Dimensions & tolerances</summary>
                  <label>
                    Annotation view
                    <select value={annotationViewId} onChange={(e) => setViewId(e.target.value)}>
                      {draft.views
                        .filter((v) => v.kind !== 'isometric')
                        .map((v) => (
                          <option key={v.id} value={v.id}>
                            {v.kind}
                          </option>
                        ))}
                    </select>
                  </label>
                  <label>
                    Geometric feature
                    <select value={featureId} onChange={(e) => setFeatureId(e.target.value)}>
                      <option value="">Select a measured feature</option>
                      {features.map((f, i) => (
                        <option
                          key={`${f.id}:${i}`}
                          value={f.id}
                          disabled={f.referenceStatus === 'ambiguous'}
                        >
                          {f.kind} {f.radiusMm ? `Ø${(2 * f.radiusMm).toFixed(3)}` : ''} ·{' '}
                          {f.origin.map((n) => n.toFixed(2)).join(', ')}
                          {f.referenceStatus === 'ambiguous' ? ' · ambiguous reference' : ''}
                        </option>
                      ))}
                    </select>
                  </label>
                  <div className="drawing-input-row">
                    <label>
                      Dimension
                      <select
                        value={dimensionKind}
                        onChange={(e) => setDimensionKind(e.target.value as DimensionSpec['kind'])}
                      >
                        <option value="extent">Overall extent</option>
                        <option value="diameter">Diameter</option>
                        <option value="radius">Radius</option>
                        <option value="distance">Feature distance</option>
                        <option value="angle">Plane angle</option>
                      </select>
                    </label>
                    <label>
                      Axis
                      <select
                        value={axis}
                        onChange={(e) => setAxis(e.target.value as 'X' | 'Y' | 'Z')}
                      >
                        <option>X</option>
                        <option>Y</option>
                        <option>Z</option>
                      </select>
                    </label>
                  </div>
                  {['distance', 'angle'].includes(dimensionKind) ? (
                    <label>
                      Second feature
                      <select
                        value={secondFeatureId}
                        onChange={(e) => setSecondFeatureId(e.target.value)}
                      >
                        <option value="">Select second feature</option>
                        {features
                          .filter((f) => f.id !== featureId && f.referenceStatus !== 'ambiguous')
                          .map((f) => (
                            <option key={f.id} value={f.id}>
                              {f.kind} · {f.origin.map((n) => n.toFixed(2)).join(', ')}
                            </option>
                          ))}
                      </select>
                    </label>
                  ) : null}
                  <div className="drawing-input-row">
                    <label>
                      Offset, sheet mm
                      <input
                        type="number"
                        min="5"
                        max="60"
                        value={offset}
                        onChange={(e) => setOffset(Number(e.target.value))}
                      />
                    </label>
                    <label className="drawing-check">
                      <input
                        type="checkbox"
                        checked={basic}
                        onChange={(e) => setBasic(e.target.checked)}
                      />
                      Basic dimension
                    </label>
                  </div>
                  <div className="drawing-input-row">
                    <label>
                      Upper +
                      <input
                        type="number"
                        min="0"
                        step="0.01"
                        value={upper}
                        onChange={(e) => setUpper(e.target.value)}
                        placeholder="mm"
                      />
                    </label>
                    <label>
                      Lower −
                      <input
                        type="number"
                        min="0"
                        step="0.01"
                        value={lower}
                        onChange={(e) => setLower(e.target.value)}
                        placeholder="mm"
                      />
                    </label>
                  </div>
                  <button className="drawing-small" onClick={addDimension}>
                    <Plus size={13} />
                    Add dimension
                  </button>
                  {draft.dimensions.map((d) => (
                    <div className="drawing-annotation" key={d.id}>
                      <span>
                        {d.kind} · {d.viewId}
                        {d.basic ? ' · basic' : ''}
                        {d.upperTolerance !== null
                          ? ` +${d.upperTolerance}/−${d.lowerTolerance}`
                          : ''}
                      </span>
                      <label>
                        Offset
                        <input
                          aria-label={`Offset for ${d.id}`}
                          type="number"
                          min="5"
                          max="60"
                          value={d.offsetMm}
                          onChange={(e) =>
                            update({
                              dimensions: draft.dimensions.map((a) =>
                                a.id === d.id ? { ...a, offsetMm: Number(e.target.value) } : a,
                              ),
                            })
                          }
                        />
                      </label>
                      <button
                        aria-label="Remove dimension"
                        onClick={() =>
                          update({ dimensions: draft.dimensions.filter((a) => a.id !== d.id) })
                        }
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  ))}
                </details>
                <details>
                  <summary>Datums & GD&T</summary>
                  <p className="drawing-note">
                    You define the manufacturing intent. Forma checks references and supported
                    annotation combinations.
                  </p>
                  <div className="drawing-input-row">
                    <label>
                      Datum label
                      <input
                        value={datumLabel}
                        maxLength={2}
                        onChange={(e) => setDatumLabel(e.target.value.toUpperCase())}
                      />
                    </label>
                    <button className="drawing-small" onClick={addDatum}>
                      Add datum
                    </button>
                  </div>
                  {draft.datums.map((d) => (
                    <div className="drawing-annotation" key={d.label}>
                      <span>
                        Datum {d.label} · {d.reference.kind}
                      </span>
                      {[0, 1].map((axis) => (
                        <label key={axis}>
                          {axis ? 'Y' : 'X'}
                          <input
                            aria-label={`Datum ${d.label} ${axis ? 'Y' : 'X'} offset`}
                            type="number"
                            min="-60"
                            max="60"
                            value={d.offset[axis]}
                            onChange={(e) =>
                              update({
                                datums: draft.datums.map((a) =>
                                  a.label === d.label
                                    ? {
                                        ...a,
                                        offset: a.offset.map((n, i) =>
                                          i === axis ? Number(e.target.value) : n,
                                        ) as [number, number],
                                      }
                                    : a,
                                ),
                              })
                            }
                          />
                        </label>
                      ))}
                      <button
                        aria-label={`Remove datum ${d.label}`}
                        onClick={() =>
                          update({ datums: draft.datums.filter((a) => a.label !== d.label) })
                        }
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  ))}
                  <label>
                    Geometric control
                    <select
                      value={characteristic}
                      onChange={(e) =>
                        setCharacteristic(e.target.value as FeatureControlFrame['characteristic'])
                      }
                    >
                      {[
                        'position',
                        'flatness',
                        'straightness',
                        'circularity',
                        'cylindricity',
                        'parallelism',
                        'perpendicularity',
                        'angularity',
                        'profile_surface',
                        'profile_line',
                        'circular_runout',
                        'total_runout',
                      ].map((c) => (
                        <option key={c} value={c}>
                          {c.replaceAll('_', ' ')}
                        </option>
                      ))}
                    </select>
                  </label>
                  <div className="drawing-input-row">
                    <label>
                      Tolerance, mm
                      <input
                        type="number"
                        min="0.001"
                        step="0.01"
                        value={tolerance}
                        onChange={(e) => setTolerance(e.target.value)}
                      />
                    </label>
                    <label>
                      Datum sequence
                      <input
                        value={datumSequence}
                        onChange={(e) => setDatumSequence(e.target.value.toUpperCase())}
                        placeholder="A, B, C"
                      />
                    </label>
                  </div>
                  <label>
                    Material condition
                    <select
                      value={materialCondition}
                      onChange={(e) =>
                        setMaterialCondition(
                          e.target.value as FeatureControlFrame['materialCondition'],
                        )
                      }
                    >
                      <option value="none">No modifier</option>
                      <option value="MMC">Maximum material (MMC)</option>
                      <option value="LMC">Least material (LMC)</option>
                    </select>
                  </label>
                  <button className="drawing-small" onClick={addControl}>
                    <Plus size={13} />
                    Add control frame
                  </button>
                  {draft.controls.map((c) => (
                    <div className="drawing-annotation" key={c.id}>
                      <span>
                        {c.characteristic.replaceAll('_', ' ')} · {c.toleranceMm} ·{' '}
                        {c.datums.join('|')}
                      </span>
                      {[0, 1].map((axis) => (
                        <label key={axis}>
                          {axis ? 'Y' : 'X'}
                          <input
                            aria-label={`Control ${c.id} ${axis ? 'Y' : 'X'} offset`}
                            type="number"
                            min="-60"
                            max="60"
                            value={c.offset[axis]}
                            onChange={(e) =>
                              update({
                                controls: draft.controls.map((a) =>
                                  a.id === c.id
                                    ? {
                                        ...a,
                                        offset: a.offset.map((n, i) =>
                                          i === axis ? Number(e.target.value) : n,
                                        ) as [number, number],
                                      }
                                    : a,
                                ),
                              })
                            }
                          />
                        </label>
                      ))}
                      <button
                        aria-label="Remove control frame"
                        onClick={() =>
                          update({ controls: draft.controls.filter((a) => a.id !== c.id) })
                        }
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  ))}
                </details>
                <label>
                  Drawing notes
                  <textarea
                    value={draft.notes.join('\n')}
                    onChange={(e) => update({ notes: e.target.value.split('\n').filter(Boolean) })}
                    rows={3}
                    placeholder="Material, finish or manufacturing notes"
                  />
                </label>
                <p className="drawing-note">
                  Draft for engineering review. Geometric tolerances describe permitted variation;
                  they are not inspection results. Changes are saved after successful regeneration.
                </p>
              </aside>
            ) : null}
          </div>
        </>
      )}
    </div>
  );
}
