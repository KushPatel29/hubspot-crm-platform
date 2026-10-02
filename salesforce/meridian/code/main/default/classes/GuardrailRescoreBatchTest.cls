/**
 * The background rescore: a sweep repairs verdicts that were edited out of step, a product's new target margin
 * rescores every deal selling it (through the Product2 trigger), and the job can be scheduled or started from a
 * context that cannot start a batch itself.
 */
@IsTest
private class GuardrailRescoreBatchTest {
    @TestSetup
    static void deal() {
        PricebookEntry kettle = GuardrailTestData.product('Kettle', 0.30);
        // 28.5% against a 30% target: below target, a rep signs.
        GuardrailTestData.deal('Sweep', null, kettle, 100, 71.5);
    }

    private static Opportunity sweep() {
        return [SELECT Id FROM Opportunity WHERE Name = 'Sweep'];
    }

    private static void editOutOfStep(Id oppId) {
        update new Opportunity(Id = oppId, Meridian_Guardrail_Verdict__c = 'above_stretch', Meridian_Approver__c = 'none');
    }

    @IsTest
    static void aSweepRepairsAVerdictThatWasEditedByHand() {
        Opportunity opp = sweep();
        Assert.areEqual('below_target', GuardrailTestData.reread(opp.Id).Meridian_Guardrail_Verdict__c);
        editOutOfStep(opp.Id);
        Test.startTest();
        Database.executeBatch(new GuardrailRescoreBatch(), GuardrailRescoreBatch.SCOPE);
        Test.stopTest();
        Opportunity repaired = GuardrailTestData.reread(opp.Id);
        Assert.areEqual('below_target', repaired.Meridian_Guardrail_Verdict__c);
        Assert.areEqual('rep', repaired.Meridian_Approver__c);
    }

    @IsTest
    static void theSweepCountsWhatItLookedAtAndWhatItChanged() {
        Opportunity opp = sweep();
        editOutOfStep(opp.Id);
        GuardrailRescoreBatch batch = new GuardrailRescoreBatch();
        Database.QueryLocatorIterator rows = batch.start(null).iterator();
        List<Opportunity> scope = new List<Opportunity>();
        while (rows.hasNext()) {
            scope.add((Opportunity) rows.next());
        }
        Assert.areEqual(1, scope.size(), 'only opportunities with lines are swept');
        batch.execute(null, scope);
        batch.execute(null, scope); // in step now: looked at again, nothing to change
        batch.finish(null);
        Assert.areEqual(2, batch.opportunities);
        Assert.areEqual(1, batch.changed);
    }

    @IsTest
    static void aNewTargetMarginRescoresEveryDealSellingTheProduct() {
        Product2 kettle = [SELECT Id FROM Product2 WHERE Name = 'Kettle'];
        Test.startTest();
        // 28.5% against a 25% target is inside the band (stretch 33%): at target, no signature.
        update new Product2(Id = kettle.Id, Meridian_Target_Margin__c = 0.25);
        Assert.isNotNull(GuardrailRescoreBatch.lastStarted, 'the trigger started a rescore');
        Test.stopTest();
        Opportunity opp = GuardrailTestData.reread(sweep().Id);
        Assert.areEqual('at_target', opp.Meridian_Guardrail_Verdict__c);
        Assert.areEqual('none', opp.Meridian_Approver__c);
    }

    @IsTest
    static void anEditThatLeavesTheTargetAloneStartsNothing() {
        Product2 kettle = [SELECT Id FROM Product2 WHERE Name = 'Kettle'];
        Test.startTest();
        update new Product2(Id = kettle.Id, Description = 'Stainless, 1.7 litres');
        Test.stopTest();
        Assert.isNull(GuardrailRescoreBatch.lastStarted);
    }

    @IsTest
    static void aRescoreOfOtherProductsLeavesThisDealAlone() {
        PricebookEntry toaster = GuardrailTestData.product('Toaster', 0.40);
        Opportunity opp = sweep();
        editOutOfStep(opp.Id);
        Test.startTest();
        Database.executeBatch(new GuardrailRescoreBatch(new Set<Id>{ toaster.Product2Id }), GuardrailRescoreBatch.SCOPE);
        Test.stopTest();
        Assert.areEqual('above_stretch', GuardrailTestData.reread(opp.Id).Meridian_Guardrail_Verdict__c);
    }

    @IsTest
    static void theSweepCanBeScheduledNightly() {
        Test.startTest();
        Id cronId = System.schedule('Meridian guardrail sweep (test)', '0 0 3 * * ?', new GuardrailRescoreBatch());
        CronTrigger cron = [SELECT CronExpression, NextFireTime FROM CronTrigger WHERE Id = :cronId];
        Test.stopTest();
        Assert.areEqual('0 0 3 * * ?', cron.CronExpression);
        Assert.areEqual(3, cron.NextFireTime.hour());
    }

    @IsTest
    static void whenTheScheduleFiresTheBatchRuns() {
        Opportunity opp = sweep();
        editOutOfStep(opp.Id);
        Test.startTest();
        new GuardrailRescoreBatch().execute((SchedulableContext) null); // what the scheduler calls at 03:00
        Test.stopTest();
        Assert.areEqual('below_target', GuardrailTestData.reread(opp.Id).Meridian_Guardrail_Verdict__c);
    }

    @IsTest
    static void theStarterBeginsTheBatchWhereABatchCannot() {
        Opportunity opp = sweep();
        editOutOfStep(opp.Id);
        Test.startTest();
        new GuardrailRescoreBatch.Starter(new GuardrailRescoreBatch()).execute(null);
        Test.stopTest();
        Assert.areEqual('below_target', GuardrailTestData.reread(opp.Id).Meridian_Guardrail_Verdict__c);
    }
}
