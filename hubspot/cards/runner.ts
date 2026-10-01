// How a card reaches its functions. Reads go to private app functions (hubspot.serverless). An action that records
// who did it goes, when the app has a signed endpoint for it, through hubspot.fetch: HubSpot signs that request and
// appends the signed-in user, so the function logs HubSpot's word for who acted, not the browser's.
import type { Runner } from './format.ts';

type Platform = {
  serverless: (name: string, options?: any) => Promise<any>;
  fetch: (url: string, options?: any) => Promise<{ status: number; json: () => Promise<any> }>;
};

export function runner(platform: Platform, signed: Record<string, string>, objectId?: number | string): Runner {
  return (name, options) => {
    const url = signed[name];
    if (!url) return platform.serverless(name, options);
    const body = { ...(options?.parameters ?? {}), objectId: String(objectId ?? '') };
    return platform.fetch(url, { method: 'POST', body }).then(async (response) => ({
      statusCode: response.status,
      body: await response.json()
        .catch(() => ({ ok: false, error: `the action endpoint answered HTTP ${response.status}` })),
    }));
  };
}
