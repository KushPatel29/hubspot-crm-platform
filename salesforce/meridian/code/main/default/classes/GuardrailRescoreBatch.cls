/**
 * Rescores opportunities in the background, 200 at a time: every opportunity with lines (a sweep, after the guardrail
 * settings change or as a nightly check that nothing has been edited out of step), or only the opportunities that
 * sell given products (a product's target margin changed, which moves the band of every line that sells it).
 *
 *   Database.executeBatch(new GuardrailRescoreBatch(), GuardrailRescoreBatch.SCOPE);          // a sweep, now
 *   System.schedule('Meridian guardrail sweep', '0 0 3 * * ?', new GuardrailRescoreBatch());  // nightly
 *
 * Without sharing on purpose: the guardrail fields are derived from the lines and must be right on every
 * opportunity, not only on the ones the person who changed a product can see. It writes nothing else.
 */
public without sharing class GuardrailRescoreBatch implements Database.Batchable<SObject>, Database.Stateful, Schedulable {
    public static final Integer SCOPE = 200;

    private final Set<Id> productIds;
    public Integer opportunities = 0;
    public Integer changed = 0;

    @TestVisible
    private static Id lastStarted;

    public GuardrailRescoreBatch() {
        this(null);
    }

    /** Only the opportunities selling these products; null for every opportunity with lines. */
    public GuardrailRescoreBatch(Set<Id> productIds) {
        this.productIds = productIds;
    }

    /**
     * Starts a rescore of the opportunities selling these products. A batch cannot start a batch, nor can a future
     * call, so from inside one (a product updated by another job) it is started through a queueable instead.
     */
    public static Id run(Set<Id> productIds) {
        GuardrailRescoreBatch batch = new GuardrailRescoreBatch(productIds);
        if (System.isBatch() || System.isFuture()) {
            lastStarted = System.enqueueJob(new Starter(batch));
        } else {
            lastStarted = Database.executeBatch(batch, SCOPE);
        }
        return lastStarted;
    }

    public Database.QueryLocator start(Database.BatchableContext context) {
        if (productIds == null) {
            return Database.getQueryLocator([
                SELECT Id FROM Opportunity WHERE Id IN (SELECT OpportunityId FROM OpportunityLineItem)
            ]);
        }
        Set<Id> products = productIds;
        return Database.getQueryLocator([
            SELECT Id FROM Opportunity
            WHERE Id IN (SELECT OpportunityId FROM OpportunityLineItem WHERE Product2Id IN :products)
        ]);
    }

    public void execute(Database.BatchableContext context, List<Opportunity> scope) {
        opportunities += scope.size();
        changed += OpportunityGuardrail.rescore(new Map<Id, Opportunity>(scope).keySet());
    }

    public void finish(Database.BatchableContext context) {
        System.debug(LoggingLevel.INFO, 'Meridian guardrail rescore: ' + opportunities + ' opportunities, '
            + changed + ' changed');
    }

    /** As a scheduled job: start the same rescore as a batch. */
    public void execute(SchedulableContext context) {
        Database.executeBatch(new GuardrailRescoreBatch(productIds), SCOPE);
    }

    /** Starts the batch from a context that cannot start one itself. */
    public class Starter implements Queueable {
        private final GuardrailRescoreBatch batch;

        public Starter(GuardrailRescoreBatch batch) {
            this.batch = batch;
        }

        public void execute(QueueableContext context) {
            Database.executeBatch(batch, SCOPE);
        }
    }
}
