import { parcels } from './data';
import { isLanguage } from './i18n';
import { validateEntry, MAX_RESULTS } from './calculations';
import type { Language, SurveyEntry } from './types';
const KEY = 'bhusetu-survey-v2';
export function loadState(): { language: Language; entries: SurveyEntry[] } {
  try {
    const s = JSON.parse(localStorage.getItem(KEY) || 'null');
    if (!s || s.version !== 2) return { language: 'en', entries: [] };
    const entries: SurveyEntry[] = [],
      seen = new Set<number>();
    for (const e of Array.isArray(s.entries) ? s.entries : []) {
      if (
        !e ||
        !e.sides ||
        typeof e.surveyor !== 'string' ||
        typeof e.notes !== 'string' ||
        !parcels.some((p) => p.id === e.parcelId) ||
        seen.has(e.parcelId) ||
        validateEntry(e).length
      )
        continue;
      entries.push(e);
      seen.add(e.parcelId);
      if (entries.length === MAX_RESULTS) break;
    }
    return { language: isLanguage(s.language) ? s.language : 'en', entries };
  } catch {
    return { language: 'en', entries: [] };
  }
}
export function persist(language: Language, entries: SurveyEntry[]): boolean {
  try {
    localStorage.setItem(KEY, JSON.stringify({ version: 2, language, entries }));
    return true;
  } catch {
    return false;
  }
}
