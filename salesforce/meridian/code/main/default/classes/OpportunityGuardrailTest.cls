/**
 * The trigger, the flow and the controller, end to end on real records: lines inserted, changed and deleted, in bulk,
 * and the approval task the "Meridian guardrail approval task" flow creates when a signature is needed.
 */
@IsTest
private class OpportunityGuardrailTest {
    // The stage values come from the org (the generated OpportunityStage set), so the test never hard-codes one.
    // Read once: the bulk test builds 200 opportunities, and a query per opportunity is 101 queries too many.
    private static String openStage;

    private static String stageName() {
        if (openStage == null) {
            openStage = [SELECT ApiName FROM OpportunityStage WHERE IsActive = TRUE AND IsClosed = FALSE
                         ORDER BY SortOrder LIMIT 1].ApiName;
        }
        return openStage;
    }

    @TestSetup
    static void catalogue() {
        Product2 kettle = new Product2(Name = 'Kettle', IsActive = true, Meridian_Target_Margin__c = 0.30);
        Product2 noTarget = new Product2(Name = 'Unbanded', IsActive = true);
        insert new List<Product2>{ kettle, noTarget };
        Id book = Test.getStandardPricebookId();
        insert new List<PricebookEntry>{
            new PricebookEntry(Pricebook2Id = book, Product2Id = kettle.Id, UnitPrice = 100, IsActive = true),
            new PricebookEntry(Pricebook2Id = book, Product2Id = noTarget.Id, UnitPrice = 100, IsActive = true)
        };
        insert new Account(Name = 'Summit Distribution');
    }

    private static Opportunity newOpportunity(String name) {
        return new Opportunity(Name = name, StageName = stageName(), CloseDate = Date.today().addDays(30),
            Pricebook2Id = Test.getStandardPricebookId());
    }

    private static OpportunityLineItem line(Id oppId, String product, Decimal price, Decimal cost, Decimal quantity) {
        PricebookEntry entry = [SELECT Id FROM PricebookEntry WHERE Product2.Name = :product LIMIT 1];
        return new OpportunityLineItem(OpportunityId = oppId, PricebookEntryId = entry.Id, UnitPrice = price,
            Unit_Cost__c = cost, Quantity = quantity);
    }

    private static Opportunity reread(Id oppId) {
        return [SELECT Meridian_Guardrail_Verdict__c, Meridian_Approver__c, Meridian_Blended_Margin__c,
                Meridian_Margin_Gap_Dollars__c FROM Opportunity WHERE Id = :oppId];
    }

    @IsTest
    static void linesSetTheVerdictAndTheApprover() {
        Opportunity opp = newOpportunity('May RFQ');
        insert opp;
        Test.startTest();
        insert new List<OpportunityLineItem>{ line(opp.Id, 'Kettle', 100, 60, 1), line(opp.Id, 'Kettle', 100, 75, 1) };
        Test.stopTest();
        Opportunity scored = reread(opp.Id);
        Assert.areEqual('below_target', scored.Meridian_Guardrail_Verdict__c);
        Assert.areEqual('sales_manager', scored.Meridian_Approver__c);
        Assert.isTrue(scored.Meridian_Blended_Margin__c == 0.325, 'blended margin: ' + scored.Meridian_Blended_Margin__c);
    }

    @IsTest
    static void aRepricedLineMovesTheVerdictAndASignatureNeedsATask() {
        Opportunity opp = newOpportunity('Repriced');
        insert opp;
        OpportunityLineItem item = line(opp.Id, 'Kettle', 100, 60, 2);
        insert item;
        Assert.areEqual('none', reread(opp.Id).Meridian_Approver__c);
        Assert.areEqual(0, [SELECT COUNT() FROM Task WHERE WhatId = :opp.Id], 'no signature, no task');
        Test.startTest();
        item.UnitPrice = 64; // 6.25% margin against a 21% floor
        update item;
        Test.stopTest();
        Opportunity scored = reread(opp.Id);
        Assert.areEqual('below_floor', scored.Meridian_Guardrail_Verdict__c);
        Assert.areEqual('vp_finance', scored.Meridian_Approver__c);
        List<Task> tasks = [SELECT Subject, Priority FROM Task WHERE WhatId = :opp.Id];
        Assert.areEqual(1, tasks.size(), 'the flow asks for the signature once');
        Assert.isTrue(tasks[0].Subject.contains('vp_finance'), tasks[0].Subject);
        Assert.areEqual('High', tasks[0].Priority);
    }

    @IsTest
    static void unscorableLinesAreLeftOutAndNoLinesClearsTheFields() {
        Opportunity opp = newOpportunity('Mixed');
        insert opp;
        OpportunityLineItem banded = line(opp.Id, 'Kettle', 100, 70, 1);
        insert new List<OpportunityLineItem>{ banded, line(opp.Id, 'Unbanded', 100, 99, 1) };
        Assert.areEqual('at_target', reread(opp.Id).Meridian_Guardrail_Verdict__c, 'the unbanded line is not scored');
        Test.startTest();
        delete banded;
        Test.stopTest();
        Opportunity cleared = reread(opp.Id);
        Assert.isNull(cleared.Meridian_Guardrail_Verdict__c);
        Assert.isNull(cleared.Meridian_Approver__c);
    }

    @IsTest
    static void twoHundredOpportunitiesInOneTransaction() {
        List<Opportunity> opps = new List<Opportunity>();
        for (Integer i = 0; i < 200; i++) {
            opps.add(newOpportunity('Bulk ' + i));
        }
        insert opps;
        PricebookEntry entry = [SELECT Id FROM PricebookEntry WHERE Product2.Name = 'Kettle' LIMIT 1];
        List<OpportunityLineItem> items = new List<OpportunityLineItem>();
        for (Opportunity opp : opps) {
            items.add(new OpportunityLineItem(OpportunityId = opp.Id, PricebookEntryId = entry.Id, UnitPrice = 100,
                Unit_Cost__c = 71.5, Quantity = 1)); // 28.5% against 30%: a rep can sign
        }
        Test.startTest();
        insert items;
        Integer queries = Limits.getQueries();
        Test.stopTest();
        Assert.isTrue(queries <= 10, 'one batch, a handful of queries: ' + queries);
        Assert.areEqual(200, [SELECT COUNT() FROM Opportunity WHERE Meridian_Approver__c = 'rep']);
    }

    @IsTest
    static void nothingToRescoreCostsNothing() {
        Integer before = Limits.getQueries();
        OpportunityGuardrail.rescore(new Set<Id>{ null });
        Assert.areEqual(before, Limits.getQueries(), 'no opportunities, no query');
    }

    // The controller runs in user mode, so it is tested as a sales user holding the two permission sets, not as the
    // admin running the tests (who has no access to fields a deploy just created).
    private static User salesUser() {
        Profile standard = [SELECT Id FROM Profile WHERE Name = 'Standard User' LIMIT 1];
        String unique = String.valueOf(Crypto.getRandomInteger()).replace('-', '');
        User u = new User(Alias = 'mrdn', Email = 'rep' + unique + '@example.com', EmailEncodingKey = 'UTF-8',
            LastName = 'Rep', LanguageLocaleKey = 'en_US', LocaleSidKey = 'en_US', ProfileId = standard.Id,
            TimeZoneSidKey = 'America/Vancouver', Username = 'rep' + unique + '@meridian.example.com');
        insert u;
        List<PermissionSetAssignment> grants = new List<PermissionSetAssignment>();
        for (PermissionSet ps : [SELECT Id FROM PermissionSet WHERE Name IN ('Crm_Platform_Meridian', 'Meridian_Guardrail_Code')]) {
            grants.add(new PermissionSetAssignment(AssigneeId = u.Id, PermissionSetId = ps.Id));
        }
        Assert.areEqual(2, grants.size(), 'both permission sets are deployed');
        insert grants;
        return u;
    }

    @IsTest
    static void theControllerShowsEachLineAndTheUnscoredOnes() {
        User rep = salesUser();
        System.runAs(rep) {
            Opportunity opp = newOpportunity('Card');
            insert opp;
            insert new List<OpportunityLineItem>{
                line(opp.Id, 'Kettle', 100, 75, 2), line(opp.Id, 'Unbanded', 10, 5, 1)
            };
            Test.startTest();
            GuardrailController.Result result = GuardrailController.scoreOpportunity(opp.Id);
            Test.stopTest();
            Assert.areEqual(1, result.lines.size());
            Assert.areEqual('Kettle', result.lines[0].name);
            Assert.areEqual('Below target', result.lines[0].score.verdict);
            Assert.areEqual(new List<String>{ 'Unbanded' }, result.unscored);
            Assert.areEqual('Sales manager', result.deal.approver);
        }
    }
}
