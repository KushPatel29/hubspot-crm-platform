// Public endpoint behind the "Next best offer" card once the app's client secret is set: the card calls it with
// hubspot.fetch, HubSpot signs the request (v3) and appends the signed-in user to the signed URL, and the decision is
// recorded under that verified name. Same decision logic as the private function (decide_offer.js).
import { appClient, parsedBody, signedUser } from '../shared/endpoint.js';
import { decide } from './decide_offer.js';

export const PATH = '/hs/serverless/decide-offer';

export async function main(context, { api, now = Date.now(), clientSecret } = {}) {
  const { who, refused } = signedUser(context, PATH, { clientSecret, now });
  if (refused) return refused;
  return decide(api ?? appClient(context), parsedBody(context), who, now);
}
