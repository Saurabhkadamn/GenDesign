'use client';
import { useState } from 'react';

export type BomRow = {
  definitionId: string;
  partNumber: string;
  revision: string;
  variant: string;
  description: string;
  material: string;
  quantity: number;
  totalQuantity?: number;
  item?: string;
  unit: string;
};
export type BomDocument = {
  status: 'draft' | 'incomplete';
  configuration: string;
  flat: BomRow[];
  structured: BomRow[];
  issues: Array<{ code: string; message: string }>;
};

export function BomPanel({ document }: { document?: BomDocument }) {
  const [mode, setMode] = useState<'flat' | 'structured'>('flat');
  if (!document) return <p>Build a design with a validated part inventory to generate its bill of materials.</p>;
  const rows = document[mode];
  return (
    <div className="bom-panel">
      <p className="bom-status">
        {document.status === 'incomplete' ? 'Part identities need review' : 'Draft for engineering review'}
        {' · '}{document.configuration === 'as_built' ? 'As built' : document.configuration}
      </p>
      {document.issues.length > 0 ? (
        <div className="bom-issues" role="status">
          {document.issues.length} part identity {document.issues.length === 1 ? 'issue' : 'issues'}.
          Assign and review part numbers, revisions and variants before procurement.
        </div>
      ) : null}
      <div className="bom-modes" aria-label="BOM layout">
        <button type="button" aria-pressed={mode === 'flat'} onClick={() => setMode('flat')}>Parts list</button>
        <button type="button" aria-pressed={mode === 'structured'} onClick={() => setMode('structured')}>Assembly structure</button>
      </div>
      <div className="bom-table-scroll" tabIndex={0} aria-label="Bill of materials table">
        <table>
          <caption>{mode === 'flat' ? 'Parts across the complete assembly' : 'Quantities per parent assembly'}</caption>
          <thead><tr>
            {mode === 'structured' ? <th scope="col">Item</th> : null}
            <th scope="col">Part number</th><th scope="col">Rev.</th><th scope="col">Description</th>
            <th scope="col">Variant</th><th scope="col">Material</th><th scope="col">Qty.</th>
            {mode === 'structured' ? <th scope="col">Total</th> : null}
          </tr></thead>
          <tbody>{rows.map((row) => <tr key={`${row.item ?? ''}:${row.definitionId}`}>
            {mode === 'structured' ? <td>{row.item}</td> : null}
            <td>{row.partNumber || <span className="bom-unassigned">Unassigned</span>}</td>
            <td>{row.revision || '—'}</td><td>{row.description}</td><td>{row.variant || '—'}</td>
            <td>{row.material || '—'}</td><td>{row.quantity} {row.unit}</td>
            {mode === 'structured' ? <td>{row.totalQuantity}</td> : null}
          </tr>)}</tbody>
        </table>
      </div>
      <p className="bom-note">Downloads are in Files. This draft counts component occurrences; it does not calculate cut lengths or material consumption.</p>
    </div>
  );
}
