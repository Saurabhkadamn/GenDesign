import type { Translate } from './i18n';
import { parcels } from './data';
import { calculateResult, visualPolygon } from './calculations';
import {
  DIRECTIONS,
  type Language,
  type Parcel,
  type SurveyEntry,
  type SurveyResult,
} from './types';
export type ViewProps = { t: Translate; language: Language };
export const num = (n: number) => n.toLocaleString('en-IN', { maximumFractionDigits: 2 });
export const signed = (n: number) => (n > 0 ? '+' : '') + num(n);
export function StatusBadge({ result, t }: { result: SurveyResult | null; t: Translate }) {
  return (
    <span className={'status ' + (result?.severity ?? 'blue')}>
      <i aria-hidden="true" />
      {t(result?.status ?? 'pending')}
    </span>
  );
}
export function Header({
  t,
  language,
  onLanguage,
  onReset,
}: ViewProps & { onLanguage: (l: Language) => void; onReset: () => void }) {
  return (
    <header>
      <div className="brand">
        <span className="brand-icon" aria-hidden="true">
          ◈
        </span>
        <div>
          <strong>BhuSetu</strong>
          <small>{t('demo')}</small>
        </div>
      </div>
      <div className="header-controls">
        <label>
          {t('language')}
          <select
            aria-label={t('language')}
            value={language}
            onChange={(e) => onLanguage(e.target.value as Language)}
          >
            <option value="en">English</option>
            <option value="mr">मराठी</option>
            <option value="hi">हिंदी</option>
          </select>
        </label>
        <button onClick={onReset}>{t('reset')}</button>
      </div>
    </header>
  );
}
export function FarmMap({
  t,
  language,
  selected,
  entries,
  preview,
  onSelect,
}: ViewProps & {
  selected: number;
  entries: SurveyEntry[];
  preview: SurveyResult | null;
  onSelect: (id: number) => void;
}) {
  return (
    <section className="map-card card">
      <div className="section-head">
        <div>
          <h2>{t('farm')}</h2>
          <p>{t('farmHint')}</p>
        </div>
        <span className="count">04</span>
      </div>
      <div className="map-scroll">
        <svg className="farm-map" viewBox="0 0 740 805" role="group" aria-label={t('farm')}>
          <defs>
            <pattern id="farm-grid" width="22" height="22" patternUnits="userSpaceOnUse">
              <path d="M22 0H0V22" fill="none" stroke="#dce5d6" strokeWidth=".5" />
            </pattern>
          </defs>
          <rect width="740" height="805" fill="#f0f3e8" />
          <rect width="740" height="805" fill="url(#farm-grid)" />
          {[false, true].map((after) => (
            <g key={String(after)} data-map-section={after ? 'surveyed' : 'recorded'}>
              <text x={after ? 551 : 189} y="37" className="section-label" textAnchor="middle">
                {t(after ? 'sectionB' : 'sectionA')}
              </text>
              {parcels.map((p, i) => {
                const x = after ? 415 : 53,
                  y = 83 + i * 166,
                  w = 270,
                  h = 166;
                const entry = entries.find((e) => e.parcelId === p.id);
                const result =
                  preview?.entry.parcelId === p.id
                    ? preview
                    : entry
                      ? calculateResult(p, entry)
                      : null;
                const visibleResult = after ? result : null;
                const points = visualPolygon(p, visibleResult, x, y, w, h)
                  .map((v) => v.join(','))
                  .join(' ');
                const sides = visibleResult?.entry.sides ?? p.sides;
                const mismatches = visibleResult
                  ? [...visibleResult.shortages, ...visibleResult.excesses]
                  : [];
                return (
                  <g
                    key={p.id}
                    data-parcel-id={p.id}
                    tabIndex={0}
                    role="button"
                    aria-pressed={selected === p.id}
                    aria-label={`${t(after ? 'sectionB' : 'sectionA')}, ${t('parcel')} ${p.id}, ${p.farmer.names[language]}`}
                    onClick={() => onSelect(p.id)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        onSelect(p.id);
                      }
                    }}
                    className={'parcel-group ' + (selected === p.id ? 'selected' : '')}
                  >
                    <rect
                      className="reference-boundary"
                      x={x}
                      y={y}
                      width={w}
                      height={h}
                      fill={after ? '#f8ead7' : p.color}
                      stroke="#829177"
                      strokeWidth="2"
                      strokeDasharray={after ? '6 4' : undefined}
                    />
                    {(!after || visibleResult) && (
                      <polygon
                        className="measured-boundary"
                        points={points}
                        fill={p.color}
                        stroke={
                          selected === p.id
                            ? '#174e39'
                            : visibleResult?.severity === 'red'
                              ? '#b24a38'
                              : '#70886c'
                        }
                        strokeWidth={selected === p.id ? 4 : 2}
                      />
                    )}
                    {after && (
                      <rect
                        x={x}
                        y={y}
                        width={w}
                        height={h}
                        fill="none"
                        stroke="#8e785e"
                        strokeWidth="1.5"
                        strokeDasharray="6 4"
                        pointerEvents="none"
                      />
                    )}
                    <text x={x + 26} y={y + 38} className="parcel-number">
                      {t('parcel')} {String(p.id).padStart(2, '0')}
                    </text>
                    <text x={x + 26} y={y + 63} className="farmer-name">
                      {p.farmer.names[language]}
                    </text>
                    <text x={x + 26} y={y + 87} className="area-label">
                      {num(visibleResult?.entry.area ?? p.area)} {t('sqm')}
                    </text>
                    {after && visibleResult ? (
                      <>
                        <text
                          x={x + 26}
                          y={y + 112}
                          className={'map-status ' + visibleResult.severity}
                        >
                          {t('mapDifference', {
                            area: signed(visibleResult.areaDifference),
                            unit: t('sqm'),
                            percent: signed(visibleResult.percentage),
                          })}
                        </text>
                        {mismatches.length ? (
                          [mismatches.slice(0, 2), mismatches.slice(2)].map(
                            (row, index) =>
                              row.length > 0 && (
                                <text
                                  key={index}
                                  x={x + 26}
                                  y={y + 128 + index * 15}
                                  className="map-status"
                                >
                                  {row
                                    .map(
                                      (d) =>
                                        `${t(d)} ${signed(visibleResult.sideDifferences[d])} ${t('m')}`,
                                    )
                                    .join(' · ')}
                                </text>
                              ),
                          )
                        ) : (
                          <text x={x + 26} y={y + 137} className="map-status">
                            {t(visibleResult.status)}
                          </text>
                        )}
                      </>
                    ) : (
                      <text x={x + 26} y={y + 113} className="map-status">
                        {t(after ? 'beforeSurvey' : 'record')}
                      </text>
                    )}
                    <text x={x + w / 2} y={y + 14} className="side-text" textAnchor="middle">
                      {t('north')} {num(sides.north)} {t('m')}
                    </text>
                    <text x={x + w / 2} y={y + h - 8} className="side-text" textAnchor="middle">
                      {t('south')} {num(sides.south)} {t('m')}
                    </text>
                    <text
                      transform={`translate(${x + w - 8} ${y + 83}) rotate(-90)`}
                      className="side-text"
                      textAnchor="middle"
                    >
                      {t('east')} {num(sides.east)} {t('m')}
                    </text>
                    <text
                      transform={`translate(${x + 12} ${y + 83}) rotate(-90)`}
                      className="side-text"
                      textAnchor="middle"
                    >
                      {t('west')} {num(sides.west)} {t('m')}
                    </text>
                  </g>
                );
              })}
            </g>
          ))}
          <path d="M366 105V62m0 0-7 13m7-13 7 13" stroke="#246d54" strokeWidth="2" fill="none" />
          <text x="366" y="54" className="side-text" textAnchor="middle">
            {t('north')}
          </text>
          <text x="366" y="777" className="side-text" textAnchor="middle">
            {t('south')}
          </text>
        </svg>
      </div>
      <div className="legend">
        <span>
          <i className="dash" />
          {t('recordLegend')}
        </span>
        <span>
          <i className="solid" />
          {t('surveyLegend')}
        </span>
      </div>
      <p className="map-note">{t('schematic')}</p>
    </section>
  );
}
export function ParcelDetails({ p, t, language }: ViewProps & { p: Parcel }) {
  return (
    <div className="parcel-details">
      <div>
        <span className="eyebrow">{t('details')}</span>
        <h2>
          {t('parcel')} {String(p.id).padStart(2, '0')}
        </h2>
        <p>{p.farmer.names[language]}</p>
      </div>
      <div className="recorded-value">
        <small>{t('recorded')}</small>
        <strong>
          {num(p.area)} <span>{t('sqm')}</span>
        </strong>
      </div>
    </div>
  );
}
export function SurveyForm({
  p,
  entry,
  t,
  onChange,
  onCalculate,
  onSave,
  onReset,
  errors,
  calculated,
}: {
  p: Parcel;
  entry: SurveyEntry;
  t: Translate;
  onChange: (e: SurveyEntry) => void;
  onCalculate: () => void;
  onSave: () => void;
  onReset: () => void;
  errors: string[];
  calculated: boolean;
}) {
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onCalculate();
      }}
      noValidate
    >
      <h3>{t('form')}</h3>
      <p className="help">{t('units')}</p>
      <div className="side-fields">
        {DIRECTIONS.map((d) => (
          <label key={d}>
            {t(d)} <span>({t('m')})</span>
            <div className="field-reference">
              {t('record')}: {num(p.sides[d])} {t('m')}
            </div>
            <input
              type="number"
              aria-invalid={errors.includes(d)}
              aria-describedby={errors.includes(d) ? 'error-' + d : undefined}
              min="0.01"
              step="0.01"
              value={Number.isNaN(entry.sides[d]) ? '' : entry.sides[d]}
              onChange={(e) =>
                onChange({
                  ...entry,
                  sides: {
                    ...entry.sides,
                    [d]: e.target.value === '' ? NaN : Number(e.target.value),
                  },
                })
              }
            />
            {errors.includes(d) && (
              <small id={'error-' + d} className="error">
                {t('positive')}
              </small>
            )}
          </label>
        ))}
      </div>
      <label>
        {t('surveyed')} ({t('sqm')})
        <input
          type="number"
          min="0.01"
          step="0.01"
          value={Number.isNaN(entry.area) ? '' : entry.area}
          aria-invalid={errors.includes('area')}
          aria-describedby={errors.includes('area') ? 'error-area' : undefined}
          onChange={(e) =>
            onChange({ ...entry, area: e.target.value === '' ? NaN : Number(e.target.value) })
          }
        />
        {errors.includes('area') && (
          <small id="error-area" className="error">
            {t('positive')}
          </small>
        )}
      </label>
      <p className="help">{t('source')}</p>
      <div className="two-fields">
        <label>
          {t('date')}
          <input
            type="date"
            value={entry.date}
            aria-invalid={errors.includes('date')}
            aria-describedby={errors.includes('date') ? 'error-date' : undefined}
            onChange={(e) => onChange({ ...entry, date: e.target.value })}
          />
          {errors.includes('date') && (
            <small className="error" id="error-date">
              {t('invalidDate')}
            </small>
          )}
        </label>
        <label>
          {t('surveyor')}
          <input
            value={entry.surveyor === 'Demo surveyor' ? t('demoName') : entry.surveyor}
            maxLength={100}
            placeholder={t('surveyorHint')}
            aria-invalid={errors.includes('surveyor')}
            aria-describedby={errors.includes('surveyor') ? 'error-surveyor' : undefined}
            onChange={(e) => onChange({ ...entry, surveyor: e.target.value })}
          />
          {errors.includes('surveyor') && (
            <small id="error-surveyor" className="error">
              {t('requiredName')}
            </small>
          )}
        </label>
      </div>
      <label>
        {t('notes')}
        <textarea
          value={entry.notes}
          maxLength={1000}
          placeholder={t('notesHint')}
          onChange={(e) => onChange({ ...entry, notes: e.target.value })}
        />
      </label>
      <div className="form-actions">
        <button type="submit" className="primary">
          {t('calculate')}
        </button>
        <button type="button" onClick={onSave} disabled={!calculated}>
          {t('save')}
        </button>
        <button type="button" className="text-button" onClick={onReset}>
          {t('resetParcel')}
        </button>
      </div>
      {!calculated && <p className="help">{t('previewHint')}</p>}
    </form>
  );
}
export function MeasurementComparison({ p, r, t }: { p: Parcel; r: SurveyResult; t: Translate }) {
  return (
    <div className="table-scroll">
      <table>
        <caption>{t('dimensions')}</caption>
        <thead>
          <tr>
            <th scope="col">{t('readings')}</th>
            <th scope="col">{t('record')}</th>
            <th scope="col">{t('survey')}</th>
            <th scope="col">{t('difference')}</th>
          </tr>
        </thead>
        <tbody>
          {DIRECTIONS.map((d) => (
            <tr key={d}>
              <th scope="row">{t(d)}</th>
              <td>
                {num(p.sides[d])} {t('m')}
              </td>
              <td>
                {num(r.entry.sides[d])} {t('m')}
              </td>
              <td
                className={
                  r.shortages.includes(d) ? 'negative' : r.excesses.includes(d) ? 'positive' : ''
                }
              >
                {signed(r.sideDifferences[d])} {t('m')}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
export function ResultCard({
  p,
  r,
  t,
  language,
  onRemove,
  onInspect,
  preview = false,
}: ViewProps & {
  p: Parcel;
  r: SurveyResult;
  onRemove?: () => void;
  onInspect?: () => void;
  preview?: boolean;
}) {
  const mismatch = [...r.shortages, ...r.excesses];
  return (
    <article className={'result-card ' + r.severity}>
      <div className="result-head">
        <div>
          <span className="eyebrow">{preview ? t('preview') : t('resultSaved')}</span>
          <h3>
            {t('parcel')} {String(p.id).padStart(2, '0')} <span>{p.farmer.names[language]}</span>
          </h3>
        </div>
        <StatusBadge result={r} t={t} />
      </div>
      <div className="area-comparison">
        <div>
          <small>{t('recorded')}</small>
          <strong>
            {num(p.area)} <span>{t('sqm')}</span>
          </strong>
        </div>
        <div>
          <small>{t('surveyed')}</small>
          <strong>
            {num(r.entry.area)} <span>{t('sqm')}</span>
          </strong>
        </div>
        <div>
          <small>{t('difference')}</small>
          <strong className={r.areaDifference < 0 ? 'negative' : ''}>
            {signed(r.areaDifference)} <span>{t('sqm')}</span>
          </strong>
          <small>{signed(r.percentage)}%</small>
        </div>
      </div>
      <p>
        {r.status === 'matched'
          ? t('matchExplanation')
          : r.status === 'boundary'
            ? t('boundaryExplanation')
            : t('explain', {
                id: p.id,
                area: num(Math.abs(r.areaDifference)),
                unit: t('sqm'),
                kind: t(r.areaDifference < 0 ? 'short' : 'extra'),
                percent: signed(r.percentage),
              })}
      </p>
      {r.primary && (
        <p className="primary-shortage">
          {t('primary', {
            direction: t(r.primary),
            amount: num(Math.abs(r.sideDifferences[r.primary])),
            unit: t('m'),
          })}
        </p>
      )}
      <p className="help">
        {mismatch.length
          ? `${t('allSides')}: ${mismatch.map((d) => `${t(d)} (${signed(r.sideDifferences[d])} ${t('m')})`).join(', ')}`
          : t('noMismatch')}
      </p>
      {!mismatch.length && r.status !== 'matched' && <p className="warning">{t('unknown')}</p>}
      {r.inconsistent && (
        <p className="warning">{t('warning', { area: num(r.approximateArea), unit: t('sqm') })}</p>
      )}
      <MeasurementComparison p={p} r={r} t={t} />
      <p className="result-meta">
        {t('date')}: {r.entry.date} · {t('surveyor')}:{' '}
        {r.entry.surveyor === 'Demo surveyor' ? t('demoName') : r.entry.surveyor}
      </p>
      {r.entry.notes && (
        <p>
          {t('notes')}: {r.entry.notes}
        </p>
      )}
      {!preview && (
        <div className="result-actions">
          <button onClick={onInspect}>{t('edit')}</button>
          <button onClick={onRemove} className="text-button">
            {t('remove')}
          </button>
        </div>
      )}
    </article>
  );
}
export function SurveyResults({
  entries,
  t,
  language,
  onRemove,
  onInspect,
}: ViewProps & {
  entries: SurveyEntry[];
  onRemove: (id: number) => void;
  onInspect: (id: number) => void;
}) {
  return (
    <section className="results-section">
      <div className="section-head">
        <div>
          <h2>{t('results')}</h2>
          <p>{t('resultsHint')}</p>
        </div>
        <strong className="count">{entries.length}/4</strong>
      </div>
      {entries.length ? (
        <div className="result-grid">
          {entries.map((e) => {
            const p = parcels.find((p) => p.id === e.parcelId)!;
            return (
              <ResultCard
                key={p.id}
                p={p}
                r={calculateResult(p, e)}
                t={t}
                language={language}
                onRemove={() => onRemove(p.id)}
                onInspect={() => onInspect(p.id)}
              />
            );
          })}
        </div>
      ) : (
        <div className="empty">{t('empty')}</div>
      )}
    </section>
  );
}
