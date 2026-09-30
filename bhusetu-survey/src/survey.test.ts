import { describe, it, expect, vi, afterEach } from 'vitest';
import { calculateResult, saveResult, validateEntry, visualPolygon } from './calculations';
import { parcels, example } from './data';
import { dictionaries, translator } from './i18n';
import { loadState, persist } from './storage';
const p = parcels[0];
describe('survey calculations', () => {
  it('uses only four unique farmers for both comparison columns', () => {
    expect(parcels).toHaveLength(4);
    expect(new Set(parcels.map((p) => p.farmer.id)).size).toBe(4);
    expect(parcels.map((p) => p.id)).toEqual([1, 2, 3, 4]);
  });
  it('calculates the required area and percentage shortage', () => {
    const r = calculateResult(p, example);
    expect(r.areaDifference).toBe(-120);
    expect(r.percentage).toBe(-5);
    expect(r.status).toBe('shortage');
    expect(r.severity).toBe('red');
  });
  it('detects a 3m north shortage and flags independent-area inconsistency', () => {
    const r = calculateResult(p, example);
    expect(r.primary).toBe('north');
    expect(r.sideDifferences.north).toBe(-3);
    expect(r.approximateArea).toBe(2340);
    expect(r.inconsistent).toBe(true);
  });
  it('shows every mismatch and picks the largest negative side', () => {
    const r = calculateResult(p, {
      ...example,
      sides: { north: 58, south: 57, east: 42, west: 36 },
    });
    expect(r.shortages).toEqual(['north', 'south', 'west']);
    expect(r.excesses).toEqual(['east']);
    expect(r.primary).toBe('west');
  });
  it('matches exact tolerance boundaries including floating point error', () => {
    const r = calculateResult(p, {
      ...example,
      area: 2412,
      sides: { north: 60.1, south: 59.9, east: 40, west: 40 },
    });
    expect(r.status).toBe('matched');
    expect(r.shortages).toEqual([]);
  });
  it('detects a boundary mismatch when area matches', () =>
    expect(calculateResult(p, { ...example, area: 2400 }).status).toBe('boundary'));
  it('rejects nonpositive and nonfinite measurements and missing metadata', () => {
    for (const value of [0, -1, NaN, Infinity]) {
      expect(validateEntry({ ...example, area: value })).toContain('area');
      expect(() =>
        calculateResult(p, { ...example, sides: { ...example.sides, east: value } }),
      ).toThrow();
    }
    expect(validateEntry({ ...example, surveyor: ' ', date: '' })).toEqual(['date', 'surveyor']);
  });
  it('limits results to 4 while allowing edits and removal', () => {
    const entries = parcels.slice(0, 4).map((p) => ({ ...example, parcelId: p.id }));
    expect(() => saveResult(entries, { ...example, parcelId: 5 })).toThrow('limit');
    expect(saveResult(entries, { ...example, area: 2200 })).toHaveLength(4);
    expect(saveResult(entries.slice(1), { ...example, parcelId: 5 })).toHaveLength(4);
  });
  it('shortens the north edge without scaling the south edge', () => {
    const pts = visualPolygon(p, calculateResult(p, example), 0, 0, 260, 144);
    expect(pts[0]).toEqual([0, 0]);
    expect(pts[1]).toEqual([247, 0]);
    expect(pts[2]).toEqual([260, 144]);
    expect(pts[3]).toEqual([0, 144]);
    expect(visualPolygon(p, null, 0, 0, 260, 144)[1]).toEqual([260, 0]);
  });
  it('does not invent a primary boundary from area-only changes', () => {
    const r = calculateResult(p, { ...example, sides: { ...p.sides } });
    expect(r.primary).toBeNull();
    expect(r.shortages).toEqual([]);
  });
  it('keeps the recorded parcel untouched while generating survey geometry', () => {
    const recorded = structuredClone(p);
    const original = visualPolygon(p, null, 0, 0, 270, 166);
    const surveyed = visualPolygon(p, calculateResult(p, example), 0, 0, 270, 166);
    expect(surveyed[1][0]).toBeLessThan(original[1][0]);
    expect(visualPolygon(p, null, 0, 0, 270, 166)).toEqual(original);
    expect(p).toEqual(recorded);
  });
  it('rejects dates that roll over to another calendar day', () => {
    expect(validateEntry({ ...example, date: '2026-02-30' })).toContain('date');
    expect(validateEntry({ ...example, date: '2026-02-28' })).not.toContain('date');
  });
});
describe('language and persistence', () => {
  afterEach(() => vi.unstubAllGlobals());
  it('switches all dictionaries immediately with identical keys', () => {
    expect(Object.keys(dictionaries.en).sort()).toEqual(Object.keys(dictionaries.mr).sort());
    expect(Object.keys(dictionaries.en).sort()).toEqual(Object.keys(dictionaries.hi).sort());
    expect(translator('en')('north')).toBe('North');
    expect(translator('mr')('north')).toBe('उत्तर');
    expect(translator('hi')('save')).toBe('सर्वेक्षण सहेजें');
    for (const lang of ['en', 'mr', 'hi'] as const)
      expect(translator(lang)('primary', { direction: 'X', amount: 3, unit: 'm' })).not.toContain(
        '{',
      );
  });
  it('restores language and results without reinterpreting measurements', () => {
    let stored = '';
    vi.stubGlobal('localStorage', {
      getItem: () => stored,
      setItem: (_: string, v: string) => (stored = v),
    });
    expect(persist('hi', [example])).toBe(true);
    expect(loadState()).toEqual({ language: 'hi', entries: [example] });
  });
  it('filters corrupt measurements, duplicates and unknown parcel IDs', () => {
    vi.stubGlobal('localStorage', {
      getItem: () =>
        JSON.stringify({
          version: 2,
          language: 'bad',
          entries: [
            null,
            example,
            example,
            { ...example, parcelId: 99 },
            { ...example, parcelId: 2, area: -1 },
          ],
        }),
    });
    expect(loadState()).toEqual({ language: 'en', entries: [example] });
  });
  it('survives corrupted JSON and unavailable storage', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => '{',
      setItem: () => {
        throw Error('full');
      },
    });
    expect(loadState()).toEqual({ language: 'en', entries: [] });
    expect(persist('en', [example])).toBe(false);
  });
});
