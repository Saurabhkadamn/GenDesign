# Smart Land Parcel Survey (BhuSetu)

A standalone React and TypeScript frontend prototype. It does not change the existing Forma CAD frontend, backend or route contracts. English, Marathi and Hindi share one translation dictionary. The farm has four fictional farmers. The left column always shows their recorded land; the right column shows the same four parcels after calculation or a saved survey. No separate farmer is assigned to the right column. Up to four saved survey results persist locally. This follows the latest visual comparison request.

## Install and run

From this directory:

```sh
npm install --workspaces=false
npm run dev
```

Open http://127.0.0.1:5178. In the existing repository the shared React, Vite, Vitest, TypeScript and ESLint dependencies can be reused without installing.

```sh
npm run test
npm run typecheck
npm run lint
npm run build
npm run preview
```

## Demo

Use **Load Parcel 1 example**, **Calculate result**, then **Save survey**. The example reports 2,400 m² recorded versus 2,280 m² entered, a −120 m² / −5% difference, and a 3 m shortage in the north-side length. The north edge shortens in the SVG while the south edge remains fixed. A warning explains that the entered area does not equal the average-side diagnostic estimate (2,340 m²).

Select any other parcel, enter measurements, date and surveyor, calculate, then save. Four slots are available. Existing saved parcels can be edited without consuming another slot. All four farmers can be surveyed independently. Reset Parcel removes that parcel's result and restores its recorded measurements. Reset Demo clears all saved results after confirmation. The example is a draft rather than an automatically saved result. Switching language does not reset measurements.

## Calculation and geometry limits

- Area difference = entered surveyed area − recorded area.
- Percentage = difference / recorded area × 100.
- Each side difference = surveyed length − recorded length.
- Negative side differences beyond 0.10 m indicate side-length shortages. All mismatches are shown. The most negative difference is the primary shortage.
- Demo tolerance is ±0.10 m per side and ±0.50% area. These are demonstration rules, not official survey standards. Mismatches of 5% or more receive a major indicator.
- The optional diagnostic area is average(north, south) × average(east, west). This formula is only a rough consistency check. It cannot establish an irregular parcel's area.
- Four side lengths do not uniquely define a parcel shape. A shortened north side does not prove that the physical north boundary moved inward. Accurate reconstruction needs coordinates, bearings, angles and appropriate surveying controls.
- The map repeats the same four parcel IDs and farmer names in both columns, using equal visual cells despite differing registered dimensions. Recorded polygons remain fixed; only surveyed polygons change. Right-hand labels show surveyed area, signed area and percentage difference, and every mismatched side. Before a survey, the right column displays the recorded outline with a pending label. Edge changes are schematic and clipped to sensible visual ranges. It is not a metric cadastral map. If only the entered area changes, the illustration scales and explicitly warns that the affected side is unknown.

No live GNSS, authentication, server sync, official database connection or legally certified land-measurement functionality is implemented. Browser storage is user-editable and not an official audit record. Surveyor names and notes remain in their entered language.

## Important files

- `src/types.ts`: parcel, farmer, survey and language types.
- `src/data.ts`: fictional Maharashtra-style farmer data and example.
- `src/i18n.ts`: all interface and explanation translations.
- `src/calculations.ts`: validation, comparisons, result cap and schematic polygon logic.
- `src/components.tsx`: accessible SVG farm map, survey form and comparison cards.
- `src/storage.ts`: versioned storage and restore validation.
- `src/survey.test.ts`: calculation, tolerance, corruption and translation tests.

Existing repository checks remain `npm test`, `npm run typecheck`, and `python -m uv run --project apps/api pytest apps/api/tests`. Repository-wide pre-existing changes are outside this standalone frontend's scope.

## Latest request and verification

The latest comparison request replaces the original eight-farmer layout: **four farmers appear twice**, once as recorded land and once after survey. Both columns use the same parcel IDs. English, Marathi and Hindi include the new column headings, differences and side labels.

After `npm run build`, open `output/BhuSetu-Prototype.html` directly for a portable, offline demo, or serve `dist`. The portable file contains the same React application and has no remote dependencies. For dependable persistence, use the localhost version, because file-based browser storage varies by browser.

Validation completed:

- Frontend: `npm run test` (16 passing), `npm run typecheck`, `npm run lint`, `npm run build`.
- Existing repository, from its root: `npm test` (9 passing), `npm run typecheck`, `python -m uv run --project apps/api pytest apps/api/tests -q` (121 passing).
- Browser: all three languages, same four farmers in both columns, saved shortage/matched/excess/boundary results, Hindi invalid-area validation, desktop/tablet/mobile layouts, and persistence across reload.

Screenshots: `screenshots/english-desktop.jpg`, `screenshots/hindi-desktop.jpg`, `screenshots/marathi-desktop.jpg`, `screenshots/marathi-mobile.jpg`.
