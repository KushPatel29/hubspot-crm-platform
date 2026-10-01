// Private function behind Meridian's "Deal margin" card: the deal's line items, each scored against its product's
// guardrail band, and the deal's verdict (worst line) and approver (most senior signature any line needs).
import { appClient, respond } from '../shared/endpoint.js';
import { scoreDeal } from '../shared/guardrail.js';

export const LINE_PROPERTIES = ['name', 'price', 'quantity', 'hs_cost_of_goods_sold', 'hs_product_id'];

export async function dealLines(api, dealId) {
  const links = await api.associated('deals', dealId, 'line_items');
  const items = await api.batchRead('line_items', links.map((l) => l.id), LINE_PROPERTIES);
  const productIds = [...new Set(items.map((i) => i.properties.hs_product_id).filter(Boolean))];
  const products = await api.batchRead('products', productIds, ['meridian_target_margin', 'name']);
  const targets = Object.fromEntries(products.map((p) => [p.id, Number(p.properties.meridian_target_margin)]));
  const lines = items
    .map((item) => ({
      id: item.id,
      name: item.properties.name,
      price: Number(item.properties.price),
      cost: Number(item.properties.hs_cost_of_goods_sold),
      quantity: Number(item.properties.quantity),
      targetMargin: targets[item.properties.hs_product_id],
    }))
    .sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
  const unscored = lines.filter((l) => !Number.isFinite(l.targetMargin) || !Number.isFinite(l.cost));
  return { lines: lines.filter((l) => !unscored.includes(l)), unscored };
}

export async function main(context, { api = appClient(context) } = {}) {
  const dealId = context.propertiesToSend?.hs_object_id ?? context.parameters?.dealId;
  if (!dealId) return respond(400, { ok: false, error: 'no deal in context' });
  const { lines, unscored } = await dealLines(api, dealId);
  const score = scoreDeal(lines);
  return respond(200, {
    ok: true,
    basis: 'Invoice margin: quoted price less cost of goods. Rebates, freight and terms live in the ERP.',
    deal: {
      blendedMarginPct: score.blendedMarginPct,
      verdict: score.verdict,
      approver: score.approver,
      gapDollars: score.gapDollars,
      worstLine: score.worstLine,
    },
    lines: lines.map((line, i) => ({ ...line, ...score.lines[i] })),
    unscored: unscored.map((l) => l.name),
  });
}
