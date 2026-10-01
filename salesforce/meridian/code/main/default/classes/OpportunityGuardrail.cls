/**
 * Keeps each Opportunity's guardrail fields in step with its line items: the verdict (worst line), the approver
 * (most senior signature), the blended margin and the margin given up. Called from the OpportunityLineItem trigger
 * with every affected opportunity at once: one query and one update per transaction, whatever the batch size.
 *
 * A line is scored only when it has a unit cost and its product a target margin; the others are left out rather
 * than guessed. An opportunity with no scored line has its guardrail fields cleared.
 */
public inherited sharing class OpportunityGuardrail {
    public static void rescore(Set<Id> opportunityIds) {
        opportunityIds.remove(null);
        if (opportunityIds.isEmpty()) {
            return;
        }
        Map<Id, List<GuardrailService.Line>> linesByOpportunity = new Map<Id, List<GuardrailService.Line>>();
        for (Id oppId : opportunityIds) {
            linesByOpportunity.put(oppId, new List<GuardrailService.Line>());
        }
        for (OpportunityLineItem item : [
            SELECT OpportunityId, UnitPrice, Quantity, Unit_Cost__c, Product2.Meridian_Target_Margin__c
            FROM OpportunityLineItem
            WHERE OpportunityId IN :opportunityIds
            ORDER BY OpportunityId, SortOrder, CreatedDate, Id
        ]) {
            if (isScorable(item)) {
                linesByOpportunity.get(item.OpportunityId).add(toLine(item));
            }
        }
        List<Opportunity> changed = new List<Opportunity>();
        for (Opportunity opp : [
            SELECT Id, Meridian_Guardrail_Verdict__c, Meridian_Approver__c, Meridian_Blended_Margin__c,
                Meridian_Margin_Gap_Dollars__c
            FROM Opportunity
            WHERE Id IN :opportunityIds
        ]) {
            Opportunity next = scored(opp.Id, linesByOpportunity.get(opp.Id));
            if (next.Meridian_Guardrail_Verdict__c != opp.Meridian_Guardrail_Verdict__c
                || next.Meridian_Approver__c != opp.Meridian_Approver__c
                || next.Meridian_Blended_Margin__c != opp.Meridian_Blended_Margin__c
                || next.Meridian_Margin_Gap_Dollars__c != opp.Meridian_Margin_Gap_Dollars__c) {
                changed.add(next);
            }
        }
        update changed;
    }

    public static Boolean isScorable(OpportunityLineItem item) {
        return item.Unit_Cost__c != null && item.UnitPrice != null && item.Quantity != null
            && item.Product2 != null && item.Product2.Meridian_Target_Margin__c != null;
    }

    public static GuardrailService.Line toLine(OpportunityLineItem item) {
        return new GuardrailService.Line(
            item.UnitPrice.doubleValue(), item.Unit_Cost__c.doubleValue(), item.Quantity.doubleValue(),
            item.Product2.Meridian_Target_Margin__c.doubleValue()
        );
    }

    private static Opportunity scored(Id oppId, List<GuardrailService.Line> lines) {
        Opportunity opp = new Opportunity(Id = oppId);
        if (lines.isEmpty()) {
            opp.Meridian_Guardrail_Verdict__c = null;
            opp.Meridian_Approver__c = null;
            opp.Meridian_Blended_Margin__c = null;
            opp.Meridian_Margin_Gap_Dollars__c = null;
            return opp;
        }
        GuardrailService.DealScore score = GuardrailService.scoreDeal(lines);
        opp.Meridian_Guardrail_Verdict__c = GuardrailService.optionValue(score.verdict);
        opp.Meridian_Approver__c = GuardrailService.optionValue(score.approver);
        // The same precision the loader writes (crm_platform/tenants/meridian.py): a fraction to four places,
        // dollars to the cent. A reload after the trigger has run therefore plans nothing.
        opp.Meridian_Blended_Margin__c = Decimal.valueOf(score.blendedMarginPct).setScale(4, RoundingMode.HALF_UP);
        opp.Meridian_Margin_Gap_Dollars__c = Decimal.valueOf(score.gapDollars).setScale(2, RoundingMode.HALF_UP);
        return opp;
    }
}
