import {
  DIRECTIONS,
  type Parcel,
  type SurveyEntry,
  type SurveyResult,
  type SideMeasurements,
} from './types';
export const SIDE_TOLERANCE = 0.1,
  AREA_TOLERANCE = 0.5,
  MAX_RESULTS = 4;
export function validateEntry(e: SurveyEntry): string[] {
  const errors: string[] = [];
  if (!Number.isFinite(e.area) || e.area <= 0) errors.push('area');
  for (const side of DIRECTIONS)
    if (!Number.isFinite(e.sides[side]) || e.sides[side] <= 0) errors.push(side);
  if (
    !/^\d{4}-\d{2}-\d{2}$/.test(e.date) ||
    Number.isNaN(Date.parse(e.date + 'T12:00:00Z')) ||
    new Date(e.date + 'T12:00:00Z').toISOString().slice(0, 10) !== e.date
  )
    errors.push('date');
  if (!e.surveyor.trim()) errors.push('surveyor');
  return errors;
}
export function calculateResult(p: Parcel, e: SurveyEntry): SurveyResult {
  if (validateEntry(e).length) throw new Error('Invalid measurement');
  const dif = Object.fromEntries(
    DIRECTIONS.map((d) => [d, e.sides[d] - p.sides[d]]),
  ) as SideMeasurements;
  const shortages = DIRECTIONS.filter((d) => dif[d] < -SIDE_TOLERANCE - 1e-8),
    excesses = DIRECTIONS.filter((d) => dif[d] > SIDE_TOLERANCE + 1e-8);
  const primary = shortages.slice().sort((a, b) => dif[a] - dif[b])[0] ?? null;
  const areaDifference = e.area - p.area,
    percentage = (areaDifference / p.area) * 100;
  const areaMatches = Math.abs(percentage) <= AREA_TOLERANCE + 1e-8;
  const status = areaMatches
    ? shortages.length || excesses.length
      ? 'boundary'
      : 'matched'
    : areaDifference < 0
      ? 'shortage'
      : 'excess';
  const largest = DIRECTIONS.reduce(
    (m, d) => Math.max(m, (Math.abs(dif[d]) / p.sides[d]) * 100),
    0,
  );
  const severity =
    status === 'matched'
      ? 'green'
      : Math.max(Math.abs(percentage), largest) >= 5 - 1e-8
        ? 'red'
        : 'orange';
  // Diagnostic only. Four side lengths alone do not uniquely determine an area.
  const approximateArea =
    (((e.sides.north + e.sides.south) / 2) * (e.sides.east + e.sides.west)) / 2;
  return {
    entry: e,
    areaDifference,
    percentage,
    sideDifferences: dif,
    shortages,
    excesses,
    primary,
    status,
    severity,
    approximateArea,
    inconsistent: (Math.abs(e.area - approximateArea) / approximateArea) * 100 > AREA_TOLERANCE,
  };
}
export function saveResult(entries: SurveyEntry[], entry: SurveyEntry): SurveyEntry[] {
  if (validateEntry(entry).length) throw new Error('Invalid measurement');
  if (!entries.some((e) => e.parcelId === entry.parcelId) && entries.length >= MAX_RESULTS)
    throw new Error('limit');
  return [...entries.filter((e) => e.parcelId !== entry.parcelId), entry];
}
export type Point = [number, number];
export function visualPolygon(
  p: Parcel,
  r: SurveyResult | null,
  x: number,
  y: number,
  w: number,
  h: number,
): Point[] {
  const pts: Point[] = [
    [x, y],
    [x + w, y],
    [x + w, y + h],
    [x, y + h],
  ];
  if (!r) return pts;
  // Schematic side-specific edge shortening, not reconstructed survey geometry.
  const edges = { north: [0, 1], east: [1, 2], south: [3, 2], west: [0, 3] } as const;
  for (const d of DIRECTIONS) {
    const ratio = Math.max(0.15, Math.min(1.22, r.entry.sides[d] / p.sides[d]));
    if (Math.abs(ratio - 1) < 1e-8) continue;
    const [a, b] = edges[d];
    pts[b] = [
      pts[a][0] + (pts[b][0] - pts[a][0]) * ratio,
      pts[a][1] + (pts[b][1] - pts[a][1]) * ratio,
    ];
  }
  // Area alone cannot locate the changed side; illustrate uniformly and disclose this.
  if (!r.shortages.length && !r.excesses.length && Math.abs(r.percentage) > AREA_TOLERANCE) {
    const scale = Math.sqrt(Math.max(0.15, Math.min(1.22, r.entry.area / p.area)));
    return pts.map(([px, py]) => [x + (px - x) * scale, y + (py - y) * scale]);
  }
  return pts;
}
