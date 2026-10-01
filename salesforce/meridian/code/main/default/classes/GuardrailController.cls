/**
 * What the dealMarginGuardrail component shows on an Opportunity: each line against its band, the verdict, the
 * approver, and the lines that could not be scored. Runs in user mode, so a user sees only fields and records their
 * permissions allow (the Crm_Platform_Meridian and Meridian_Guardrail_Code permission sets grant them).
 */
public with sharing class GuardrailController {
    public class LineView {
        @AuraEnabled public Id id;
        @AuraEnabled public String name;
        @AuraEnabled public Decimal quantity;
        @AuraEnabled public Decimal unitPrice;
        @AuraEnabled public GuardrailService.LineScore score;
    }

    public class Result {
        @AuraEnabled public GuardrailService.DealScore deal;
        @AuraEnabled public List<LineView> lines = new List<LineView>();
        @AuraEnabled public List<String> unscored = new List<String>();
        @AuraEnabled public String basis =
            'Invoice margin: quoted price less unit cost. Rebates, freight and terms live in the ERP.';
    }

    @AuraEnabled(cacheable=true)
    public static Result scoreOpportunity(Id opportunityId) {
        Result result = new Result();
        List<GuardrailService.Line> lines = new List<GuardrailService.Line>();
        for (OpportunityLineItem item : [
            SELECT Id, Name, Quantity, UnitPrice, Unit_Cost__c, Product2.Name, Product2.Meridian_Target_Margin__c
            FROM OpportunityLineItem
            WHERE OpportunityId = :opportunityId
            WITH USER_MODE
            ORDER BY SortOrder, CreatedDate, Id
        ]) {
            String name = item.Product2 != null && item.Product2.Name != null ? item.Product2.Name : item.Name;
            if (!OpportunityGuardrail.isScorable(item)) {
                result.unscored.add(name);
                continue;
            }
            LineView view = new LineView();
            view.id = item.Id;
            view.name = name;
            view.quantity = item.Quantity;
            view.unitPrice = item.UnitPrice;
            result.lines.add(view);
            lines.add(OpportunityGuardrail.toLine(item));
        }
        result.deal = GuardrailService.scoreDeal(lines);
        for (Integer i = 0; i < result.lines.size(); i++) {
            result.lines[i].score = result.deal.lines[i];
        }
        return result;
    }
}
