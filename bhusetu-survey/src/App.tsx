import { useEffect, useState } from 'react';
import { parcels, example, freshEntry } from './data';
import { translator } from './i18n';
import { calculateResult, saveResult, validateEntry } from './calculations';
import { loadState, persist } from './storage';
import type { Language, SurveyEntry, SurveyResult } from './types';
import {
  Header,
  FarmMap,
  ParcelDetails,
  SurveyForm,
  ResultCard,
  SurveyResults,
} from './components';
export default function App() {
  const [initial] = useState(loadState),
    [language, setLanguage] = useState<Language>(initial.language),
    [entries, setEntries] = useState<SurveyEntry[]>(initial.entries);
  const [selected, setSelected] = useState(1),
    [draft, setDraft] = useState<SurveyEntry>(() => {
      const saved = initial.entries.find((entry) => entry.parcelId === 1) ?? example;
      return { ...saved, sides: { ...saved.sides } };
    }),
    [preview, setPreview] = useState<SurveyResult | null>(null),
    [errors, setErrors] = useState<string[]>([]),
    [message, setMessage] = useState<'saved' | 'limit' | 'draftLoaded' | null>(null),
    [storageFailed, setStorageFailed] = useState(false);
  const t = translator(language),
    p = parcels.find((p) => p.id === selected)!;
  useEffect(() => {
    document.documentElement.lang = language;
    setStorageFailed(!persist(language, entries));
  }, [language, entries]);
  const select = (id: number) => {
    const p = parcels.find((p) => p.id === id)!;
    setSelected(id);
    const e = entries.find((e) => e.parcelId === id) ?? freshEntry(p);
    setDraft({ ...e, sides: { ...e.sides } });
    setPreview(null);
    setErrors([]);
    setMessage(null);
  };
  const change = (e: SurveyEntry) => {
    setDraft(e);
    setPreview(null);
    setErrors([]);
    setMessage(null);
  };
  const calculate = () => {
    const issues = validateEntry(draft);
    setErrors(issues);
    setMessage(null);
    setPreview(issues.length ? null : calculateResult(p, draft));
  };
  const save = () => {
    if (!preview) return;
    try {
      setEntries(saveResult(entries, draft));
      setPreview(null);
      setMessage('saved');
    } catch {
      setMessage('limit');
    }
  };
  const remove = (id: number) => {
    setEntries(entries.filter((e) => e.parcelId !== id));
    if (id === selected) {
      setPreview(null);
      setDraft(freshEntry(p));
    }
    setMessage(null);
  };
  const resetParcel = () => {
    remove(selected);
    setDraft(freshEntry(p));
    setErrors([]);
  };
  const reset = () => {
    if (!confirm(t('confirmReset'))) return;
    setEntries([]);
    setSelected(1);
    setDraft({ ...example, sides: { ...example.sides } });
    setPreview(null);
    setErrors([]);
    setMessage(null);
  };
  const loadExample = () => {
    setSelected(1);
    setDraft({ ...example, sides: { ...example.sides } });
    setPreview(null);
    setErrors([]);
    setMessage('draftLoaded');
  };
  const exportResults = () => {
    const data = entries.map((e) => ({
      parcel: parcels.find((p) => p.id === e.parcelId),
      result: calculateResult(
        parcels.find((p) => p.id === e.parcelId)!,
        e,
      ),
      prototype: true,
    }));
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }),
    );
    const a = document.createElement('a');
    a.href = url;
    a.download = 'bhusetu-survey-results.json';
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 500);
  };
  return (
    <>
      <Header t={t} language={language} onLanguage={setLanguage} onReset={reset} />
      <main>
        <div className="page-heading">
          <div>
            <div className="eyebrow">{t('demo')}</div>
            <h1>{t('title')}</h1>
            <p>{t('subtitle')}</p>
          </div>
          <button onClick={loadExample}>{t('example')}</button>
        </div>
        <div className="summary">
          <div>
            <strong>{parcels.length}</strong>
            <span>{t('total')}</span>
          </div>
          <div>
            <strong>
              {entries.length}
              <small>/4</small>
            </strong>
            <span>{t('savedCount')}</span>
          </div>
          <div>
            <strong>
              {
                entries.filter(
                  (e) =>
                    calculateResult(
                      parcels.find((p) => p.id === e.parcelId)!,
                      e,
                    ).status !== 'matched',
                ).length
              }
            </strong>
            <span>{t('reviewCount')}</span>
          </div>
          <p>{t('tolerance')}</p>
        </div>
        <div role="status" aria-live="polite">
          {message && (
            <p className={message === 'limit' ? 'notice error-notice' : 'notice'}>{t(message)}</p>
          )}
          {storageFailed && <p className="notice error-notice">{t('storage')}</p>}
        </div>
        <div className="workspace">
          <FarmMap
            t={t}
            language={language}
            selected={selected}
            entries={entries}
            preview={preview}
            onSelect={select}
          />
          <section className="card form-card">
            <ParcelDetails p={p} t={t} language={language} />
            <SurveyForm
              p={p}
              entry={draft}
              t={t}
              onChange={change}
              onCalculate={calculate}
              onSave={save}
              onReset={resetParcel}
              errors={errors}
              calculated={!!preview}
            />
            {preview && <ResultCard p={p} r={preview} t={t} language={language} preview />}
          </section>
        </div>
        <div className="results-tools">
          <button disabled={!entries.length} onClick={exportResults}>
            {t('export')}
          </button>
        </div>
        <SurveyResults
          entries={entries}
          t={t}
          language={language}
          onRemove={remove}
          onInspect={select}
        />
        <p className="help colour-legend">{t('colourLegend')}</p>
        <footer>
          {t('disclaimer')}
          <br />
          {t('angle')}
        </footer>
      </main>
    </>
  );
}
