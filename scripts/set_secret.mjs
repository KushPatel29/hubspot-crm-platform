// Save an app-function secret into a portal from the clipboard: copy the value in HubSpot, then run
//
//   npm run set-secret -- HUBSPOT_CLIENT_SECRET 247549241
//
// It uses the HubSpot CLI's own login and secrets API (the same calls `hs secret add/update` makes) but reads the
// value from the clipboard instead of a hidden prompt, where a paste is easy to lose. The value is never printed;
// only its length is, so you can tell the right thing was copied.
import { execFileSync } from 'node:child_process';
import { platform } from 'node:os';
import { addSecret, fetchSecrets, updateSecret } from '@hubspot/local-dev-lib/api/secrets';
import { getConfigAccountById, getConfigAccountByName } from '@hubspot/local-dev-lib/config';

const [name, account] = process.argv.slice(2);
if (!name || !account) {
  console.error('usage: npm run set-secret -- <SECRET_NAME> <account id or name>');
  process.exit(2);
}

function clipboard() {
  if (platform() === 'win32') {
    return execFileSync('powershell', ['-NoProfile', '-Command', 'Get-Clipboard -Raw'], { encoding: 'utf8' });
  }
  return execFileSync(platform() === 'darwin' ? 'pbpaste' : 'xclip', platform() === 'darwin' ? [] : ['-o'],
    { encoding: 'utf8' });
}

const value = clipboard().trim();
if (!value || /\s/.test(value)) {
  console.error('The clipboard does not hold a single value. Copy the secret in HubSpot and run this again.');
  process.exit(1);
}
const config = /^\d+$/.test(account) ? getConfigAccountById(Number(account)) : getConfigAccountByName(account);
if (!config) {
  console.error(`No HubSpot CLI account "${account}". Run: npx hs account list`);
  process.exit(1);
}
const accountId = config.accountId ?? config.portalId;
const { data } = await fetchSecrets(accountId);
const exists = (data.results ?? []).includes(name);
await (exists ? updateSecret : addSecret)(accountId, name, value);
console.log(`${name} ${exists ? 'updated' : 'added'} in portal ${accountId} (${value.length} characters; not printed).`);
