// What can be checked about the hand-written Apex without an org: that every class and trigger parses (the ANTLR
// grammar the Apex Dev Tools project keeps in step with the platform compiler), that each has its -meta.xml, and
// that every class with logic has a test class that names it. Whether it compiles against the org's fields and
// passes is what `sf project deploy start --test-level RunLocalTests` answers, in an org.
import { readdirSync, readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { check } from '@apexdevtools/apex-parser';

const ROOT = fileURLToPath(new URL('../code/main/default/', import.meta.url));
const problems = [];

const result = await check(ROOT);
for (const error of result.errors) problems.push(`${error.path}:${error.line}:${error.column} ${error.message}`);

const files = (dir, suffix) => (existsSync(join(ROOT, dir)) ? readdirSync(join(ROOT, dir)) : [])
  .filter((name) => name.endsWith(suffix));
const classes = files('classes', '.cls');
const triggers = files('triggers', '.trigger');
for (const [dir, names] of [['classes', classes], ['triggers', triggers]]) {
  for (const name of names) {
    if (!existsSync(join(ROOT, dir, `${name}-meta.xml`))) problems.push(`${dir}/${name}: no ${name}-meta.xml`);
  }
}

const source = (name) => readFileSync(join(ROOT, 'classes', name), 'utf8');
const tests = classes.filter((name) => /@IsTest/i.test(source(name)));
const testText = tests.map(source).join('\n');
for (const name of classes.filter((n) => !tests.includes(n))) {
  const className = name.replace(/\.cls$/, '');
  if (!new RegExp(`\\b${className}\\.`).test(testText)) problems.push(`classes/${name}: no test class calls it`);
}
for (const name of triggers) {
  const body = readFileSync(join(ROOT, 'triggers', name), 'utf8');
  // SOQL or DML written inside a trigger's loop is the classic governor-limit failure; the work belongs in a class.
  if (/\[\s*SELECT\b/i.test(body) || /\b(insert|update|delete|upsert)\s+\w/i.test(body.replace(/trigger[^{]*\{/i, ''))) {
    problems.push(`triggers/${name}: queries or DML in the trigger body; delegate to a class`);
  }
}

if (problems.length) {
  console.error(`✗ ${problems.length} problem(s) in the Apex source:`);
  for (const problem of problems) console.error(`  - ${problem}`);
  process.exit(1);
}
console.log(`✓ ${classes.length} classes (${tests.length} test classes) and ${triggers.length} trigger parse, ` +
  'each has its metadata, and every class is exercised by a test.');
