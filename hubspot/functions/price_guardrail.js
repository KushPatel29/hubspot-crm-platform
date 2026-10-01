// Custom workflow action "Check the price guardrail" (Meridian), served by a public endpoint function.
// A workflow calls it when a deal enters "Pricing review": it verifies HubSpot's signature, scores the deal's lines,
// writes the verdict to the deal, and returns output fields the workflow branches on (needs_approval, approver).
import { client } from '../shared/hubspot-api.js';
import { optionValue, scoreDeal } from '../shared/guardrail.js';
import { authenticate, parsedBody, respond } from '../shared/endpoint.js';
import { dealLines } from './deal_margin.js';

export const PATH = '/_hcms/api/price-guardrail';

export async function main(context, { api, now = Date.now(), secret } = {}) {
  const auth = authenticate(context, PATH, { secret, now });
  if (!auth.ok) return respond(401, { error: auth.reason });
  const request = parsedBody(context);
  const dealId = request.object?.objectId;
  if (!dealId) return respond(400, { error: 'no deal in the request' });
  const hubspot = api ?? client();
  const { lines, unscored } = await dealLines(hubspot, dealId);
  if (lines.length === 0) {
    return respond(200, { outputFields: { hs_execution_state: 'FAIL_CONTINUE', verdict: 'no_lines',
      needs_approval: 'false', approver: 'none', blended_margin: 0 } });
  }
  const score = scoreDeal(lines);
  await hubspot.patch(`/crm/v3/objects/deals/${dealId}`, { properties: {
    meridian_guardrail_verdict: optionValue(score.verdict),
    meridian_approver: optionValue(score.approver),
    meridian_blended_margin: score.blendedMarginPct.toFixed(4),
    meridian_margin_gap_dollars: score.gapDollars.toFixed(2),
  } });
  return respond(200, { outputFields: {
    verdict: optionValue(score.verdict),
    approver: optionValue(score.approver),
    needs_approval: String(score.approver !== 'None'),
    blended_margin: Number(score.blendedMarginPct.toFixed(4)),
    unscored_lines: unscored.length,
  } });
}
