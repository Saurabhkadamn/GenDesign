"""Independent oracle for the upstream four-bar fixture, not a solver-status test."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def measure(data):
    markers = {name: (None, value) for name, value in data['groundMarkers'].items()}
    for part in data['parts']:
        for name, value in part['markers'].items():
            markers[name] = (part['name'], value)
    residuals = []
    for frame in data['frames']:
        world = {}
        for name, (owner, local) in markers.items():
            point = np.array(local['position'])
            rotation = np.array(local['rotation'])
            if owner is not None:
                pose = frame['poses'][owner]
                orientation = np.array(pose['rotation'])
                point = np.array(pose['position']) + orientation @ point
                rotation = orientation @ rotation
            world[name] = (point, rotation[:, 2])
        for joint in data['joints']:
            a, b = world[joint['markerI']], world[joint['markerJ']]
            residuals.append((float(np.linalg.norm(a[0] - b[0]) * 1000),
                              float(np.linalg.norm(np.cross(a[1], b[1])))))
    times = np.array([f['time'] for f in data['frames']])
    # This fixture specifies Part1 as the crank and 2*pi*time as its driver.
    crank = next(p['name'] for p in data['parts'] if p['name'].endswith('/Part1'))
    rotations = [np.array(f['poses'][crank]['rotation']) for f in data['frames']]
    angles = np.unwrap([np.arctan2(r[1, 0], r[0, 0]) for r in rotations])
    driver_error = float(np.max(np.abs(angles - angles[0] - 2 * np.pi * times)))
    max_position = max(r[0] for r in residuals)
    max_axis = max(r[1] for r in residuals)
    assert len(data['parts']) == 3 and len(data['joints']) == 4
    assert len(times) >= 101 and abs(times[0]) < 1e-10 and times[-1] >= 1 - 1e-9
    # Native output includes the input state and the solved initial state,
    # both at t=0. Measure both; do not mistake the input for solved output.
    assert abs(times[1]) < 1e-10
    assert np.allclose(np.diff(times[1:]), .01, atol=1e-9)
    assert max_position < 1e-4, f'Closed-loop position residual {max_position} mm'
    assert max_axis < 1e-7, f'Axis alignment error {max_axis}'
    assert driver_error < 1e-7, f'Driver angle error {driver_error} rad'
    return {'fixture': 'OndselSolver testapp/fourbar.asmt', 'passed': True,
            'movingParts': 3, 'revoluteJoints': 4, 'frameCount': len(times),
            'timeRangeSeconds': [float(times[0]), float(times[-1])],
            'samplesPastRequestedEnd': int(sum(times > 1 + 1e-9)),
            'inputStateFrames': 1, 'solvedFrames': len(times) - 1,
            'maxClosedLoopPositionResidualMm': max_position,
            'maxAxisCrossProductNorm': max_axis,
            'maxDriverAngleErrorRad': driver_error,
            'checks': len(residuals),
            'limitations': ['One planar closed-loop mechanism, not a general mechanism qualification.',
                            'Upstream includes an input-state frame before solved frames; adapter must identify it explicitly.',
                            'Positions measured from returned poses and local markers independently of solver success flags.']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    content = args.input.read_bytes()
    result = measure(json.loads(content))
    result['sourceCommit'] = '4be80eef02a3486cda0d78f3ccbb308d207a9639'
    result['rawEvidenceSha256'] = hashlib.sha256(content).hexdigest()
    result['build'] = {'compiler': 'GCC 16.2.0, MinGW 14 UCRT', 'optimization': '-O2',
                       'assertionsEnabled': True, 'platform': 'Windows x86_64'}
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
