// Which HubSpot app each business gets. One shared library of cards and functions; each business's portal gets its
// own private app with only the parts that fit it, and only the scopes those parts use. build.mjs turns this into
// one deployable HubSpot project per business under projects/.

export const SIGNED_ENDPOINT_SECRETS = ['HUBSPOT_CLIENT_SECRET', 'ENDPOINT_BASE_URL'];
// A function marked `signed: '<path>'` records who acted. While a portal's app has no client secret it is deployed as
// a private function and logs the card's word for the user ("unverified"). Once the secret exists, build.mjs deploys
// <name>_signed at that endpoint instead and the card calls it with hubspot.fetch, which HubSpot signs with the
// signed-in user in the URL.

const guardrailOptions = [
  ['above_stretch', 'Above stretch'], ['at_target', 'At target'], ['below_target', 'Below target'],
  ['below_floor', 'Below floor'], ['loss_making', 'Loss-making'], ['no_lines', 'No priced lines'],
];
const approverOptions = [
  ['none', 'None'], ['rep', 'Rep'], ['sales_manager', 'Sales manager'],
  ['commercial_director', 'Commercial director'], ['vp_finance', 'VP Finance'],
];
const lifecycleOptions = [
  ['lead', 'Lead'], ['marketingqualifiedlead', 'Marketing qualified lead'],
  ['salesqualifiedlead', 'Sales qualified lead'], ['opportunity', 'Opportunity'], ['customer', 'Customer'],
  ['evangelist', 'Evangelist'],
];
const toOptions = (pairs) => pairs.map(([value, label]) => ({ value, label }));
// The loader's write path (functions/provision.js) runs under the app's token, so each app also carries the read and
// write scopes its tenant's schema and records need, and nothing more.
const PROVISION = { name: 'provision', endpoint: 'provision', secrets: ['PROVISION_KEY'] };
const crud = (...objects) => objects.flatMap((o) => [`crm.objects.${o}.read`, `crm.objects.${o}.write`]);
const schemas = (...objects) => objects.flatMap((o) => [`crm.schemas.${o}.read`, `crm.schemas.${o}.write`]);
const unique = (...lists) => [...new Set(lists.flat())];

// The other way an integration reaches HubSpot: one OAuth app installed into many portals. It has no cards or
// functions; it exists so the loader can also run over OAuth (python -m crm_platform connect <tenant>, then
// --via oauth), with the scopes the loader's calls need across the tenants. The redirect is a local port the
// connect command listens on, which HubSpot allows over http for localhost only.
export const CONNECTOR = {
  name: 'CRM platform connector',
  description: 'Lets the CRM platform loader reach an approved portal over OAuth: schema and record reads and '
    + 'writes for the objects its models declare.',
  redirectUrls: ['http://localhost:3000/oauth-callback'],
  scopes: unique(['oauth', 'e-commerce', 'crm.schemas.line_items.read'],
    crud('contacts', 'companies', 'deals', 'products', 'line_items', 'custom'),
    schemas('contacts', 'companies', 'deals', 'custom')),
};

export const APPS = {
  scalelab: {
    name: 'ScaleLab CRM platform',
    description: 'Revenue truth on every GrowthOps contact, a lifecycle action that never moves a contact backwards, '
      + 'and signed webhook deliveries.',
    scopes: ['oauth', 'crm.objects.contacts.read', 'crm.objects.contacts.write', 'crm.objects.deals.read'],
    cards: [{ component: 'RevenueTruthCard', uid: 'revenue_truth_card', name: 'Revenue truth (GrowthOps)',
      description: 'Collected cash, the campaign each attribution model credits, and renewal risk.',
      objectTypes: ['contacts'], needs: [] }],
    functions: [
      { name: 'advance_lifecycle', endpoint: 'advance-lifecycle', secrets: SIGNED_ENDPOINT_SECRETS },
      { name: 'webhook_receiver', endpoint: 'webhooks', secrets: SIGNED_ENDPOINT_SECRETS },
    ],
    workflowActions: [{
      uid: 'advance_lifecycle_action',
      function: 'advance_lifecycle',
      objectTypes: ['CONTACT'],
      inputFields: [{ typeDefinition: { name: 'target_stage', type: 'enumeration', fieldType: 'select',
        options: toOptions(lifecycleOptions) }, supportedValueTypes: ['STATIC_VALUE'], isRequired: true }],
      outputFields: [
        { typeDefinition: { name: 'outcome', type: 'enumeration', fieldType: 'select',
          options: toOptions([['moved', 'Moved'], ['kept', 'Kept'], ['rejected', 'Rejected']]) } },
        { typeDefinition: { name: 'from_stage', type: 'string', fieldType: 'text' } },
        { typeDefinition: { name: 'reason', type: 'string', fieldType: 'text' } },
      ],
      labels: {
        actionName: 'Advance lifecycle stage (never backwards)',
        actionDescription: 'Moves the contact to the chosen lifecycle stage only if that is forward. GrowthOps\' '
          + 'field contract: lifecycle stage is shared and moves forward only.',
        actionCardContent: 'Advance lifecycle to {{target_stage}}',
        inputFieldLabels: { target_stage: 'Target stage' },
        inputFieldDescriptions: { target_stage: 'The stage to move the contact to. A move backwards is refused, not made.' },
        outputFieldLabels: { outcome: 'Outcome', from_stage: 'Previous stage', reason: 'Reason' },
      },
    }],
    webhooks: {
      function: 'webhook_receiver',
      crmObjects: [
        { subscriptionType: 'object.creation', objectType: 'contact' },
        { subscriptionType: 'object.propertyChange', objectType: 'contact', propertyName: 'lifecyclestage' },
        { subscriptionType: 'object.propertyChange', objectType: 'contact', propertyName: 'growthops_renewal_risk' },
        { subscriptionType: 'object.deletion', objectType: 'contact' },
        { subscriptionType: 'object.merge', objectType: 'contact' },
      ],
      hubEvents: [{ subscriptionType: 'contact.privacyDeletion' }],
    },
  },

  meridian: {
    name: 'Meridian Supply pricing guardrail',
    description: 'Line-by-line deal margin against each product\'s guardrail band, and a workflow action that '
      + 'decides who has to sign.',
    // Product and line-item properties have no crm.schemas.* scopes; the legacy e-commerce scope covers them.
    scopes: unique(['oauth', 'e-commerce', 'crm.schemas.line_items.read'],
      crud('contacts', 'companies', 'deals', 'products', 'line_items'), schemas('contacts', 'companies', 'deals')),
    cards: [{ component: 'DealMarginCard', uid: 'deal_margin_card', name: 'Deal margin guardrail',
      description: 'Each line against its floor and target margin, the verdict and the approver needed.',
      objectTypes: ['deals'], needs: ['run'] }],
    functions: [
      { name: 'deal_margin' },
      { name: 'price_guardrail', endpoint: 'price-guardrail', secrets: SIGNED_ENDPOINT_SECRETS },
      PROVISION,
    ],
    workflowActions: [{
      uid: 'price_guardrail_action',
      function: 'price_guardrail',
      objectTypes: ['DEAL'],
      inputFields: [],
      outputFields: [
        { typeDefinition: { name: 'verdict', type: 'enumeration', fieldType: 'select',
          options: toOptions(guardrailOptions) } },
        { typeDefinition: { name: 'approver', type: 'enumeration', fieldType: 'select',
          options: toOptions(approverOptions) } },
        { typeDefinition: { name: 'needs_approval', type: 'enumeration', fieldType: 'select',
          options: toOptions([['true', 'Yes'], ['false', 'No']]) } },
        { typeDefinition: { name: 'blended_margin', type: 'number', fieldType: 'number' } },
      ],
      labels: {
        actionName: 'Check the price guardrail',
        actionDescription: 'Scores every line against its product\'s margin band (floor = target - 9 points), '
          + 'writes the verdict to the deal and returns who has to approve it.',
        actionCardContent: 'Check the price guardrail',
        inputFieldLabels: {},
        inputFieldDescriptions: {},
        outputFieldLabels: { verdict: 'Verdict', approver: 'Approver', needs_approval: 'Needs approval',
          blended_margin: 'Blended margin' },
      },
    }],
  },

  crosssell: {
    name: 'Cross-sell next best offer',
    description: 'The recommendation engine\'s next-best offers on every account, turned into deals in one click.',
    // crm.schemas.deals.write: the loader adds the deal idempotency key (crm_platform_key) the decide_offer function
    // writes, so a second deal for the same offer is refused by HubSpot itself.
    scopes: unique(['oauth', 'e-commerce'], crud('contacts', 'companies', 'deals', 'products', 'line_items', 'custom'),
      schemas('contacts', 'companies', 'deals', 'custom')),
    cards: [{ component: 'NextBestOfferCard', uid: 'next_best_offer_card', name: 'Next best offer',
      description: 'Eligible offers with the reason and the value, accepted into deals or dismissed with a reason.',
      objectTypes: ['companies'], needs: ['run', 'portalId', 'actor'] }],
    functions: [{ name: 'company_offers' }, { name: 'decide_offer', signed: 'decide-offer' }, PROVISION],
  },

  aml: {
    name: 'Investigation case desk',
    description: 'Case evidence, deadline and shared counterparties on every investigation case, with audited '
      + 'stage moves. Synthetic data; an educational simulation, not a compliance tool.',
    scopes: unique(['oauth'], crud('contacts', 'companies', 'custom'), schemas('contacts', 'companies', 'custom')),
    cards: [{ component: 'CaseEvidenceCard', uid: 'case_evidence_card', name: 'Case evidence',
      description: 'Deadline, typology hypothesis against its lawful lookalike, shared counterparties, next moves.',
      objectTypes: ['p_investigation_case'], needs: ['run', 'actor'] }],
    functions: [{ name: 'case_network' }, { name: 'case_transition', signed: 'case-transition' }, PROVISION],
  },
};
