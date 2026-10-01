/**
 * Rescores an opportunity's guardrail whenever its lines change. All the work is in OpportunityGuardrail, which
 * handles every opportunity in the batch with one query and one update.
 */
trigger OpportunityLineItemGuardrail on OpportunityLineItem (after insert, after update, after delete, after undelete) {
    Set<Id> opportunityIds = new Set<Id>();
    for (OpportunityLineItem item : Trigger.isDelete ? Trigger.old : Trigger.new) {
        opportunityIds.add(item.OpportunityId);
    }
    OpportunityGuardrail.rescore(opportunityIds);
}
