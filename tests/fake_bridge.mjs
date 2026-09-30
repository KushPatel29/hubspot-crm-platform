// A stand-in for scripts/hs_bridge.mjs that speaks the same protocol, for testing BridgeTransport without HubSpot.
import readline from 'node:readline';

const MARKER = '\u0001bridge ';
const account = process.argv[2];
if (account === 'missing') {
  process.stderr.write('hs_bridge: no CLI account "missing"\n');
  process.exit(2);
}
console.log('library noise that is not a response');
process.stdout.write(MARKER + JSON.stringify({ ready: true, accountId: 4242 }) + '\n');
for await (const line of readline.createInterface({ input: process.stdin })) {
  const { id, method, path, body } = JSON.parse(line);
  const status = path === '/missing' ? 404 : 200;
  process.stdout.write(MARKER + JSON.stringify({ id, status, headers: { 'X-HubSpot-RateLimit-Remaining': 9 },
    body: { method, path, echoed: body ?? null } }) + '\n');
}
