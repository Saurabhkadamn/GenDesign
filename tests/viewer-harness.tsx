// Local renderer verification with a real CAD fixture; never mounted by the application.
import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { CadScene } from '../apps/web/src/components/cad-viewer';
declare const __NEMOTRON__: boolean;
const fixture = __NEMOTRON__
  ? { name: 'Nemotron plate · reviewed source, real STEP/GLB', selected: 'plate', hidden: 'plate' }
  : { name: 'Real CAD assembly fixture', selected: 'left', hidden: 'right' };

function Harness() {
  const [selected, setSelected] = useState<string | null>(null);
  const [hidden, setHidden] = useState<string[]>([]);
  const [wireframe, setWireframe] = useState(false);
  const [fitVersion, setFitVersion] = useState(0);
  return (
    <main
      style={{
        height: '100vh',
        display: 'grid',
        gridTemplateRows: '64px 1fr',
        background: '#faf9f6',
        fontFamily: 'Arial',
        color: '#34412e',
      }}
    >
      <header
        style={{
          display: 'flex',
          gap: 16,
          alignItems: 'center',
          padding: '0 24px',
          background: 'white',
        }}
      >
        <strong>Renderer verification · {fixture.name}</strong>
        <button onClick={() => setSelected(fixture.selected)}>Select {fixture.selected}</button>
        <button onClick={() => setHidden(hidden.length ? [] : [fixture.hidden])}>
          Toggle {fixture.hidden}
        </button>
        <button onClick={() => setWireframe(!wireframe)}>Toggle wireframe</button>
        <button onClick={() => setFitVersion(fitVersion + 1)}>Fit</button>
        <output>{selected ?? 'No selection'}</output>
      </header>
      <div style={{ minHeight: 0 }}>
        <CadScene
          url="/fixture.glb"
          selected={selected ? [selected] : []}
          hidden={hidden}
          onSelect={setSelected}
          wireframe={wireframe}
          fitVersion={fitVersion}
          grid
        />
      </div>
    </main>
  );
}
createRoot(document.getElementById('root')!).render(<Harness />);
