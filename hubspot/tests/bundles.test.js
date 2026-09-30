import { describe, expect, it } from 'vitest';
import { createRequire } from 'node:module';
import { readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

describe('generated function bundles', () => {
  it('export main as CommonJS, the way HubSpot loads an app function', () => {
    const require = createRequire(import.meta.url);
    const root = join(dirname(fileURLToPath(import.meta.url)), '..', 'projects');
    const bundles = readdirSync(root).flatMap((tenant) => readdirSync(join(root, tenant, 'src/app/functions'))
      .filter((f) => f.endsWith('.js')).map((f) => join(root, tenant, 'src/app/functions', f)));
    expect(bundles).toHaveLength(8);
    for (const bundle of bundles) expect(typeof require(bundle).main).toBe('function');
  });
});
