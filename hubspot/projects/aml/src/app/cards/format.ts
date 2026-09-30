// Formatting shared by the cards. Values from HubSpot arrive as strings; empty means "not set", never zero.

export type Runner = (name: string, options?: { parameters?: Record<string, unknown>; propertiesToSend?: string[] })
  => Promise<any>;

export function num(value: string | null | undefined): number | null {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

export function money(value: number | null, digits = 0): string {
  if (value === null) return '—';
  return value.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: digits,
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
