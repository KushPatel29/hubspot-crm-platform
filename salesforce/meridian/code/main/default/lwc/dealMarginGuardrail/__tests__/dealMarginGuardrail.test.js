import { createElement } from 'lwc';
import DealMarginGuardrail from 'c/dealMarginGuardrail';
import scoreOpportunity from '@salesforce/apex/GuardrailController.scoreOpportunity';

jest.mock(
    '@salesforce/apex/GuardrailController.scoreOpportunity',
    () => {
        const { createApexTestWireAdapter } = require('@salesforce/sfdx-lwc-jest');
        return { default: createApexTestWireAdapter(jest.fn()) };
    },
    { virtual: true }
);

// The shape GuardrailController.Result serialises to.
const RESULT = {
    basis: 'Invoice margin: quoted price less unit cost. Rebates, freight and terms live in the ERP.',
    unscored: ['Unbanded'],
    deal: { blendedMarginPct: 0.3, verdict: 'Below target', approver: 'Sales manager', gapDollars: 10, worstLine: 1 },
    lines: [
        { id: '1', name: 'Kettle', quantity: 1, unitPrice: 100,
            score: { marginPct: 0.4, verdict: 'Above stretch', approver: 'None', floor: 0.21, target: 0.3 } },
        { id: '2', name: 'Toaster', quantity: 2, unitPrice: 100,
            score: { marginPct: 0.25, verdict: 'Below target', approver: 'Sales manager', floor: 0.21, target: 0.3 } }
    ]
};

function mount() {
    const element = createElement('c-deal-margin-guardrail', { is: DealMarginGuardrail });
    element.recordId = '006000000000001AAA';
    document.body.appendChild(element);
    return element;
}

const text = (element, selector) => element.shadowRoot.querySelector(selector)?.textContent;
const settled = () => Promise.resolve();

describe('c-deal-margin-guardrail', () => {
    afterEach(() => {
        while (document.body.firstChild) document.body.removeChild(document.body.firstChild);
        jest.clearAllMocks();
    });

    it('asks Apex for the record it is on and shows a spinner until the answer arrives', async () => {
        const element = mount();
        await settled();
        expect(scoreOpportunity.getLastConfig()).toEqual({ opportunityId: '006000000000001AAA' });
        expect(element.shadowRoot.querySelector('lightning-spinner')).not.toBeNull();
    });

    it('names the verdict, the approver and the line that sets it', async () => {
        const element = mount();
        scoreOpportunity.emit(RESULT);
        await settled();
        expect(text(element, '.headline')).toBe('Below target: Sales manager must approve');
        expect(text(element, '.worst')).toBe('Toaster sets the verdict at 25.0%.');
        expect(text(element, '.blended')).toBe('30.0%');
        expect(text(element, '.gap')).toBe('$10');
        expect(element.shadowRoot.querySelector('[role="alert"]').className).toContain('slds-theme_warning');
        const rows = [...element.shadowRoot.querySelectorAll('li.line')].map((row) => [
            row.querySelector('.name').textContent, row.querySelector('.figures').textContent,
            row.querySelector('.slds-badge').textContent, row.querySelector('.slds-badge').className]);
        expect(rows).toEqual([
            ['Kettle', 'Margin 40.0% · floor 21% · target 30%', 'Above stretch',
                'slds-badge slds-shrink-none slds-theme_success'],
            ['Toaster', 'Margin 25.0% · floor 21% · target 30%', 'Below target',
                'slds-badge slds-shrink-none slds-theme_warning']
        ]);
        expect(text(element, '.unscored')).toBe('Not scored (no product target or unit cost): Unbanded.');
    });

    it('shows a clean deal as needing no signature, and a floor breach as an error', async () => {
        const element = mount();
        scoreOpportunity.emit({ ...RESULT, unscored: [],
            deal: { ...RESULT.deal, verdict: 'At target', approver: 'None', worstLine: 0 } });
        await settled();
        expect(text(element, '.headline')).toBe('At target: no signature needed');
        expect(text(element, '.worst')).toBe('Every line is at or above its target margin.');
        expect(element.shadowRoot.querySelector('[role="alert"]').className).toContain('slds-theme_success');
        expect(text(element, '.unscored')).toBe('');

        scoreOpportunity.emit({ ...RESULT, deal: { ...RESULT.deal, verdict: 'Below floor', approver: 'VP Finance' } });
        await settled();
        expect(text(element, '.headline')).toBe('Below floor: VP Finance must approve');
        expect(element.shadowRoot.querySelector('[role="alert"]').className).toContain('slds-theme_error');
    });

    it('says so when the opportunity has no priced lines, and shows an Apex error instead of a blank card', async () => {
        const element = mount();
        scoreOpportunity.emit({ ...RESULT, lines: [], unscored: [],
            deal: { blendedMarginPct: 0, verdict: 'At target', approver: 'None', gapDollars: 0, worstLine: -1 } });
        await settled();
        expect(text(element, '.empty')).toBe('This opportunity has no priced line items yet.');
        expect(element.shadowRoot.querySelector('ul.lines')).toBeNull();

        scoreOpportunity.error({ message: 'You do not have access to Unit_Cost__c' });
        await settled();
        expect(text(element, '.error')).toBe('You do not have access to Unit_Cost__c');
    });
});
