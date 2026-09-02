import { defineConfig, globalIgnores } from 'eslint/config';
import nextVitals from 'eslint-config-next/core-web-vitals';
import nextTs from 'eslint-config-next/typescript';
export default defineConfig([
  ...nextVitals,
  ...nextTs,
  { settings: { next: { rootDir: 'apps/web/' } } },
  globalIgnores([
    '.tools/**',
    '**/.next/**',
    '**/.venv/**',
    'runtimes/**',
    'test-results/**',
    '**/next-env.d.ts',
    'apps/web/src/app/.well-known/**',
    'apps/web/src/components/ai-elements/**',
    'apps/web/src/components/ui/**',
  ]),
]);
