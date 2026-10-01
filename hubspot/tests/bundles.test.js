import { describe, expect, it } from 'vitest';
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { APPS } from '../apps.mjs';

describe('generated function bundles', () => {
  it('export main as CommonJS, the way HubSpot loads an app function', () => {
    const require = createRequire(import.meta.url);
    const root = join(dirname(fileURLToPath(import.meta.url)), '..', 'projects');
    const bundles = readdirSync(root).flatMap((tenant) => readdirSync(join(root, tenant, 'src/app/functions'))
      .filter((f) => f.endsWith('.js')).map((f) => join(root, tenant, 'src/app/functions', f)));
    expect(bundles).toHaveLength(Object.values(APPS).reduce((n, app) => n + app.functions.length, 0));
    for (const bundle of bundles) expect(typeof require(bundle).main).toBe('function');
  });
});

describe('a portal whose app has its client secret', () => {
  it('serves the actions that record who acted from signed endpoints, and the card calls them with hubspot.fetch', () => {
    const here = join(dirname(fileURLToPath(import.meta.url)), '..');
    const temp = mkdtempSync(join(tmpdir(), 'crm-platform-verified-'));
    try {
      const evidence = join(temp, 'evidence');
      for (const [tenant, id] of [['aml', 111], ['crosssell', 222]]) {
        mkdirSync(join(evidence, tenant), { recursive: true });
        writeFileSync(join(evidence, tenant, 'deploy.json'), JSON.stringify({ accountId: id,
          endpointBaseUrl: `https://${id}.hs-sites-na2.com`, secrets: { ENDPOINT_BASE_URL: true,
            HUBSPOT_CLIENT_SECRET: tenant === 'aml' } }));
      }
      execFileSync(process.execPath, ['build.mjs', '--evidence', evidence, '--out', join(temp, 'out')], { cwd: here });
      const app = (tenant) => join(temp, 'out', tenant, 'src', 'app');
      const read = (path) => readFileSync(path, 'utf8');

      // AML has the secret: the signed endpoint replaces the private function, and only its URL may be fetched.
      const signed = JSON.parse(read(join(app('aml'), 'functions', 'case_transition_signed-hsmeta.json')));
      expect(signed.config).toEqual({ entrypoint: '/app/functions/case_transition_signed.js',
        secretKeys: ['HUBSPOT_CLIENT_SECRET', 'ENDPOINT_BASE_URL'], endpoint: { path: 'case-transition', methods: ['POST'] } });
      expect(existsSync(join(app('aml'), 'functions', 'case_transition-hsmeta.json'))).toBe(false);
      expect(JSON.parse(read(join(app('aml'), 'app-hsmeta.json'))).config.permittedUrls.fetch)
        .toEqual(['https://111.hs-sites-na2.com/hs/serverless/']);
      const entry = read(join(app('aml'), 'cards', 'CaseEvidenceCardEntry.tsx'));
      expect(entry).toContain('run={runner(hubspot, SIGNED, context.crm.objectId)}');
      expect(entry).toContain('"case_transition": "https://111.hs-sites-na2.com/hs/serverless/case-transition"');
      expect(existsSync(join(app('aml'), 'cards', 'runner.ts'))).toBe(true);

      // Cross-sell does not yet: the private function stays, nothing may be fetched, and the card says so in its log.
      expect(existsSync(join(app('crosssell'), 'functions', 'decide_offer-hsmeta.json'))).toBe(true);
      expect(existsSync(join(app('crosssell'), 'functions', 'decide_offer_signed-hsmeta.json'))).toBe(false);
      expect(JSON.parse(read(join(app('crosssell'), 'app-hsmeta.json'))).config.permittedUrls.fetch).toEqual([]);
      expect(read(join(app('crosssell'), 'cards', 'NextBestOfferCardEntry.tsx'))).toContain('hubspot.serverless(name, options)');
    } finally {
      rmSync(temp, { recursive: true, force: true });
    }
  }, 60_000);
});
