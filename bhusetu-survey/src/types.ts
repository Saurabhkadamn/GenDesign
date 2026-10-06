export type Language = 'en' | 'mr' | 'hi';
export const DIRECTIONS = ['north', 'south', 'east', 'west'] as const;
export type Direction = (typeof DIRECTIONS)[number];
export type SideMeasurements = Record<Direction, number>;
export interface Farmer {
  id: number;
  names: Record<Language, string>;
}
export interface Parcel {
  id: number;
  farmer: Farmer;
  area: number;
  sides: SideMeasurements;
  color: string;
}
export interface SurveyEntry {
  parcelId: number;
  area: number;
  sides: SideMeasurements;
  date: string;
  surveyor: string;
  notes: string;
}
export type Status = 'matched' | 'shortage' | 'excess' | 'boundary';
export interface SurveyResult {
  entry: SurveyEntry;
  areaDifference: number;
  percentage: number;
  sideDifferences: SideMeasurements;
  shortages: Direction[];
  excesses: Direction[];
  primary: Direction | null;
  status: Status;
  severity: 'green' | 'orange' | 'red';
  approximateArea: number;
  inconsistent: boolean;
}
