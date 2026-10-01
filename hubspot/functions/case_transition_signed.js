// Public endpoint behind the "Case evidence" card once the app's client secret is set: the card calls it with
// hubspot.fetch, HubSpot signs the request (v3) and appends the signed-in user to the signed URL, and the move is
// logged under that verified name. Same rules as the private function (case_transition.js).
import { appClient, parsedBody, signedUser } from '../shared/endpoint.js';
import { transition } from './case_transition.js';

export const PATH = '/hs/serverless/case-transition';

export async function main(context, { api, now = Date.now(), clientSecret } = {}) {
  const { who, refused } = signedUser(context, PATH, { clientSecret, now });
  if (refused) return refused;
  const body = parsedBody(context);
  return transition(api ?? appClient(context), { caseId: body.caseId ?? body.objectId, toStage: body.toStage,
    note: body.note }, who, now);
}
