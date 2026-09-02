'use client';
import { Component, Suspense, useEffect, useMemo, useState, type ReactNode } from 'react';
import { Canvas, type ThreeEvent } from '@react-three/fiber';
import {
  Bounds,
  Grid,
  OrbitControls,
  useGLTF,
  GizmoHelper,
  GizmoViewport,
  useBounds,
} from '@react-three/drei';
import * as THREE from 'three';
import { api } from '@/lib/api';

function ViewModel({
  url,
  hidden,
  selected,
  onSelect,
  fitVersion,
  wireframe,
}: {
  url: string;
  hidden: string[];
  selected: string[];
  onSelect: (id: string | null) => void;
  fitVersion: number;
  wireframe: boolean;
}) {
  const { scene } = useGLTF(url);
  const bounds = useBounds();
  const clone = useMemo(() => {
    const model = scene.clone(true);
    model.traverse((node) => {
      if (node instanceof THREE.Mesh) {
        node.material = Array.isArray(node.material)
          ? node.material.map((m) => m.clone())
          : node.material.clone();
      }
    });
    return model;
  }, [scene]);
  useEffect(
    () => () => {
      clone.traverse((node) => {
        if (node instanceof THREE.Mesh) {
          const materials = Array.isArray(node.material) ? node.material : [node.material];
          materials.forEach((m) => m.dispose());
        }
      });
    },
    [clone],
  );
  useEffect(() => {
    clone.traverse((node) => {
      node.visible = !hidden.includes(node.name);
      if (node instanceof THREE.Mesh) {
        const mats = Array.isArray(node.material) ? node.material : [node.material];
        for (const mat of mats) {
          if (mat instanceof THREE.MeshStandardMaterial) {
            mat.wireframe = wireframe;
            mat.emissive.set(selected.includes(node.name) ? '#61744b' : '#000000');
            mat.emissiveIntensity = 0.22;
            mat.roughness = 0.68;
            mat.metalness = 0.12;
          }
        }
      }
    });
  }, [clone, hidden, selected, wireframe]);
  useEffect(() => {
    bounds.refresh(clone).clip().fit();
  }, [bounds, clone, fitVersion]);
  function select(event: ThreeEvent<MouseEvent>) {
    event.stopPropagation();
    let node: THREE.Object3D | null = event.object;
    while (node && (!node.name || node.name.startsWith('geometry_'))) node = node.parent;
    onSelect(node?.name ?? null);
  }
  return <primitive object={clone} onClick={select} />;
}
class ViewerBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <div className="viewer-error">
        The 3D preview could not be rendered. Try reloading, or download the STEP file.
      </div>
    ) : (
      this.props.children
    );
  }
}
export function CadViewer({
  artifactId,
  hidden,
  selected,
  onSelect,
  fitVersion,
  wireframe,
  grid,
}: {
  artifactId: string;
  hidden: string[];
  selected: string[];
  onSelect: (id: string | null) => void;
  fitVersion: number;
  wireframe: boolean;
  grid: boolean;
}) {
  const [state, setState] = useState<{ url?: string; error?: string }>({});
  useEffect(() => {
    let active = true;
    api<{ url: string }>(`artifacts/${artifactId}`)
      .then((data) => {
        if (active) setState(data);
      })
      .catch(() => {
        if (active) setState({ error: 'This preview is unavailable. Try reopening the project.' });
      });
    return () => {
      active = false;
    };
  }, [artifactId]);
  if (state.error) return <div className="viewer-error">{state.error}</div>;
  if (!state.url) return <div className="viewer-loading">Loading geometry…</div>;
  return (
    <CadScene
      url={state.url}
      hidden={hidden}
      selected={selected}
      onSelect={onSelect}
      fitVersion={fitVersion}
      wireframe={wireframe}
      grid={grid}
    />
  );
}

/** The renderer consumes only a validated mesh URL and read-only inspection controls. */
export function CadScene({
  url,
  hidden,
  selected,
  onSelect,
  fitVersion,
  wireframe,
  grid,
}: {
  url: string;
  hidden: string[];
  selected: string[];
  onSelect: (id: string | null) => void;
  fitVersion: number;
  wireframe: boolean;
  grid: boolean;
}) {
  return (
    <ViewerBoundary key={url}>
      <Canvas
        camera={{ position: [160, -220, 170], up: [0, 0, 1], fov: 38 }}
        dpr={[1, 2]}
        onPointerMissed={() => onSelect(null)}
        gl={{ antialias: true }}
      >
        <ambientLight intensity={1.4} />
        <directionalLight position={[200, -200, 300]} intensity={2.4} />
        <directionalLight position={[-100, 150, 80]} intensity={1} />
        <Suspense fallback={null}>
          <Bounds fit clip observe margin={1.4}>
            <ViewModel
              url={url}
              hidden={hidden}
              selected={selected}
              onSelect={onSelect}
              fitVersion={fitVersion}
              wireframe={wireframe}
            />
          </Bounds>
        </Suspense>
        {grid && (
          <Grid
            infiniteGrid
            rotation={[Math.PI / 2, 0, 0]}
            sectionSize={50}
            cellSize={10}
            cellThickness={0.4}
            sectionThickness={0.7}
            cellColor="#d3d9c9"
            sectionColor="#c4cdb9"
            fadeDistance={1500}
          />
        )}
        <OrbitControls makeDefault enableDamping dampingFactor={0.12} />
        <GizmoHelper alignment="bottom-right" margin={[60, 60]}>
          <GizmoViewport axisColors={['#ba7970', '#93a27d', '#769ab1']} labelColor="#ffffff" />
        </GizmoHelper>
      </Canvas>
    </ViewerBoundary>
  );
}
