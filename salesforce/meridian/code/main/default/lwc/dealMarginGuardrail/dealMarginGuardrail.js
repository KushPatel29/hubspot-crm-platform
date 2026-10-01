// Deal margin guardrail on the Opportunity record page: the Salesforce twin of HubSpot's DealMarginCard
// (hubspot/cards/DealMarginCard.tsx). Each line against its product's band, the verdict (worst line) and who has
// to sign, scored in Apex by GuardrailController with the same rule as the HubSpot card.
import { LightningElement, api, wire } from 'lwc';
import { refreshApex } from '@salesforce/apex';
import scoreOpportunity from '@salesforce/apex/GuardrailController.scoreOpportunity';

const TONE = {
    'Above stretch': 'slds-badge slds-theme_success',
    'At target': 'slds-badge slds-theme_success',
    'Below target': 'slds-badge slds-theme_warning',
    'Below floor': 'slds-badge slds-theme_error',
    'Loss-making': 'slds-badge slds-theme_error'
};

function pct(value, digits = 1) {
    return value === null || value === undefined ? '—' : `${(value * 100).toFixed(digits)}%`;
}

function money(value) {
    if (value === null || value === undefined) return '—';
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(value);
}

export default class DealMarginGuardrail extends LightningElement {
    @api recordId;
    result;
    error;
    wiredResult;

    @wire(scoreOpportunity, { opportunityId: '$recordId' })
    receive(value) {
        this.wiredResult = value;
        const { data, error } = value;
        if (data) {
            this.result = data;
            this.error = undefined;
        } else if (error) {
            this.error = error.body?.message ?? 'The margin check could not run.';
            this.result = undefined;
        }
    }

    get loading() {
        return !this.result && !this.error;
    }

    get hasLines() {
        return Boolean(this.result && this.result.lines.length);
    }

    get noLines() {
        return Boolean(this.result && !this.result.lines.length);
    }

    get blended() {
        return pct(this.result.deal.blendedMarginPct);
    }

    get gap() {
        return money(this.result.deal.gapDollars);
    }

    get headline() {
        const { verdict, approver } = this.result.deal;
        return approver === 'None' ? `${verdict}: no signature needed` : `${verdict}: ${approver} must approve`;
    }

    get alertClass() {
        const { verdict, approver } = this.result.deal;
        const theme = verdict === 'Below floor' || verdict === 'Loss-making' ? 'slds-theme_error'
            : approver === 'None' ? 'slds-theme_success' : 'slds-theme_warning';
        return `slds-notify slds-notify_alert ${theme}`;
    }

    get worstText() {
        const { worstLine, approver } = this.result.deal;
        if (worstLine < 0 || approver === 'None') return 'Every line is at or above its target margin.';
        const line = this.result.lines[worstLine];
        return `${line.name} sets the verdict at ${pct(line.score.marginPct)}.`;
    }

    get rows() {
        return this.result.lines.map((line) => ({
            id: line.id,
            name: line.name,
            margin: pct(line.score.marginPct),
            band: `${pct(line.score.floor, 0)} / ${pct(line.score.target, 0)}`,
            verdict: line.score.verdict,
            badgeClass: TONE[line.score.verdict] ?? 'slds-badge'
        }));
    }

    get unscoredText() {
        const names = this.result?.unscored ?? [];
        return names.length ? `Not scored (no product target or unit cost): ${names.join(', ')}.` : '';
    }

    handleRefresh() {
        return refreshApex(this.wiredResult);
    }
}
