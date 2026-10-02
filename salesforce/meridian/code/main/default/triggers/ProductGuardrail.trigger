/**
 * A product's target margin is the middle of its band, so changing it can change the verdict of every opportunity
 * that sells the product. The work is asynchronous (OpportunityGuardrail.targetMarginsChanged starts
 * GuardrailRescoreBatch): one product can sit on thousands of lines.
 */
trigger ProductGuardrail on Product2 (after update) {
    OpportunityGuardrail.targetMarginsChanged(Trigger.new, Trigger.oldMap);
}
