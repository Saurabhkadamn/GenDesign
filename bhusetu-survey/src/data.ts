import type { Parcel, SurveyEntry } from './types';
const names = [
  ['Ramesh Patil', 'रमेश पाटील', 'रमेश पाटिल'],
  ['Sunita Jadhav', 'सुनीता जाधव', 'सुनीता जाधव'],
  ['Suresh Shinde', 'सुरेश शिंदे', 'सुरेश शिंदे'],
  ['Meena Deshmukh', 'मीना देशमुख', 'मीना देशमुख'],
];
const widths = [60, 52.5, 45, 50];
const colors = ['#e3ead3', '#e3dbc7', '#dce9e2', '#e8dccf'];
export const parcels: Parcel[] = widths.map((w, i) => ({
  id: i + 1,
  farmer: { id: i + 1, names: { en: names[i][0], mr: names[i][1], hi: names[i][2] } },
  area: w * 40,
  sides: { north: w, south: w, east: 40, west: 40 },
  color: colors[i],
}));
// Independent entered area is intentional: this example has an inconsistency warning.
export const example: SurveyEntry = {
  parcelId: 1,
  area: 2280,
  sides: { north: 57, south: 60, east: 40, west: 40 },
  date: '2026-09-30',
  surveyor: 'Demo surveyor',
  notes: '',
};
export const freshEntry = (p: Parcel): SurveyEntry => ({
  parcelId: p.id,
  area: p.area,
  sides: { ...p.sides },
  date: new Date().toLocaleDateString('en-CA'),
  surveyor: '',
  notes: '',
});
