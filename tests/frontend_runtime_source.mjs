import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';

const root = new URL('../frontend/src/', import.meta.url);
const order = JSON.parse(readFileSync(new URL('runtime/module-order.json', root), 'utf8'));
const withoutImports = source => source
  .replace(/^import .*?;\n/gm, '')
  .replace(/^export \{.*?\} from .*?;\n/gm, '')
  .replace(/^export /gm, '');

/** Retain the established controller behavior tests while source is modularized.
 * React components and real ES module wiring are covered by frontend/tests.
 */
export function readRuntimeSource() {
  const libraries = ['lib/store.ts', 'lib/resources.ts', 'lib/api.ts', 'lib/sse.ts', 'app/navigation.ts']
    .map(file => withoutImports(stripTypeScriptTypes(readFileSync(new URL(file, root), 'utf8'), { mode: 'transform' })));
  return [...libraries, ...order.map(name => withoutImports(readFileSync(new URL(`runtime/${name}.js`, root), 'utf8')))].join('\n');
}
