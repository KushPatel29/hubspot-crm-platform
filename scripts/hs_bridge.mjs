// A long-running bridge from Python to HubSpot through the HubSpot CLI's own authentication.
//
// The CLI (`npx hs account auth`) stores a personal access key per account and exchanges it for short-lived access
// tokens. This script reuses exactly that, through @hubspot/local-dev-lib, so the Python tools need no key of their
// own and never see one. Protocol: one JSON request per line on stdin, {id, method, path, body}; one JSON response
// per line on stdout, prefixed with a marker so any logging from the library cannot be mistaken for a response:
// {id, status, headers, body}. A network failure answers status 0.
//
//   node scripts/hs_bridge.mjs <account name or id>
import readline from 'node:readline';
import { http } from '@hubspot/local-dev-lib/http';
import {
  getConfigAccountById,
  getConfigAccountByName,
  getConfigDefaultAccount,
} from '@hubspot/local-dev-lib/config';

const MARKER = '\u0001bridge ';
const wanted = process.argv[2];
const account = !wanted
  ? getConfigDefaultAccount()
  : /^\d+$/.test(wanted)
    ? getConfigAccountById(Number(wanted))
    : getConfigAccountByName(wanted);
if (!account) {
  process.stderr.write(`hs_bridge: no CLI account "${wanted ?? 'default'}"; run npx hs account auth\n`);
  process.exit(2);
}
const accountId = account.accountId ?? account.portalId;
const send = (message) => process.stdout.write(MARKER + JSON.stringify(message) + '\n');
send({ ready: true, accountId });

const verbs = { GET: 'get', POST: 'post', PUT: 'put', PATCH: 'patch', DELETE: 'delete' };
const lines = readline.createInterface({ input: process.stdin });
for await (const line of lines) {
  if (!line.trim()) continue;
  const { id, method, path, body } = JSON.parse(line);
  const options = { url: path.replace(/^\//, '') };
  if (body !== undefined && body !== null) {
    options.data = body;
    options.headers = { 'Content-Type': 'application/json' };
  }
  try {
    const response = await http[verbs[method]](accountId, options);
    send({ id, status: response.status, headers: response.headers ?? {}, body: response.data ?? null });
  } catch (error) {
    if (error && typeof error.status === 'number') {
      send({ id, status: error.status, headers: error.headers ?? {}, body: error.data ?? null });
    } else {
      send({ id, status: 0, headers: {}, body: null, error: String(error?.message ?? error).slice(0, 200) });
    }
  }
}
