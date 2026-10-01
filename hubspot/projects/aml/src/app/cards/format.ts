// Formatting shared by the cards. Values from HubSpot arrive as strings; empty means "not set", never zero.

export type Runner = (name: string, options?: { parameters?: Record<string, unknown>; propertiesToSend?: string[] })
  => Promise<any>;

// An app function answers { statusCode, body }; hubspot.serverless resolves to that object. Accept a bare body
// too, so a card does not break if the runtime ever unwraps it.
export async function call(run: Runner, name: string, options?: Parameters<Runner>[1]): Promise<any> {
  const result = await run(name, options);
  return result && typeof result === 'object' && 'statusCode' in result && 'body' in result ? result.body : result;
}

export function num(value: string | null | undefined): number | null {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

// currency: what the amount is in. CAD is shown as CA$ so it is never read as US dollars.
export function money(value: number | null, digits = 0, currency = 'USD'): string {
  if (value === null) return '—';
  return value.toLocaleString('en-US', { style: 'currency', currency, maximumFractionDigits: digits,
    minimumFractionDigits: digits });
}

export function pct(value: number | null, digits = 1): string {
  return value === null ? '—' : `${(value * 100).toFixed(digits)}%`;
}

export function day(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(/^\d+$/.test(value) ? Number(value) : value);
  return Number.isNaN(date.getTime()) ? '—' : date.toISOString().slice(0, 10);
}

export function label(value: string | null | undefined): string {
  if (!value) return '—';
  return value.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());
}
