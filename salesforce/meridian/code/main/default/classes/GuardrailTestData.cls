/**
 * Records the guardrail tests share: a product with a target margin and its standard price-book entry, an open
 * opportunity with a line, and a sales user holding the two permission sets (for code that runs in user mode).
 */
@IsTest
public class GuardrailTestData {
    private static String openStage;

    /** The org's first open stage: the generated OpportunityStage set, never a hard-coded name. */
    public static String openStage() {
        if (openStage == null) {
            openStage = [SELECT ApiName FROM OpportunityStage WHERE IsActive = TRUE AND IsClosed = FALSE
                         ORDER BY SortOrder LIMIT 1].ApiName;
        }
        return openStage;
    }

    public static PricebookEntry product(String name, Decimal targetMargin) {
        Product2 product = new Product2(Name = name, IsActive = true, Meridian_Target_Margin__c = targetMargin);
        insert product;
        PricebookEntry entry = new PricebookEntry(Pricebook2Id = Test.getStandardPricebookId(), Product2Id = product.Id,
            UnitPrice = 100, IsActive = true);
        insert entry;
        return entry;
    }

    /** An open opportunity with one line on the entry's product; the trigger scores it on insert. */
    public static Opportunity deal(String name, String key, PricebookEntry entry, Decimal price, Decimal cost) {
        Opportunity opp = new Opportunity(Name = name, StageName = openStage(), CloseDate = Date.today().addDays(30),
            Pricebook2Id = Test.getStandardPricebookId(), Crm_Platform_Key__c = key);
        insert opp;
        insert new OpportunityLineItem(OpportunityId = opp.Id, PricebookEntryId = entry.Id, UnitPrice = price,
            Unit_Cost__c = cost, Quantity = 1);
        return opp;
    }

    public static Opportunity reread(Id oppId) {
        return [SELECT Meridian_Guardrail_Verdict__c, Meridian_Approver__c, Meridian_Blended_Margin__c
                FROM Opportunity WHERE Id = :oppId];
    }

    /** A Standard User holding Crm_Platform_Meridian (the fields) and Meridian_Guardrail_Code (the Apex). */
    public static User salesUser() {
        Profile standard = [SELECT Id FROM Profile WHERE Name = 'Standard User' LIMIT 1];
        String unique = String.valueOf(Crypto.getRandomInteger()).replace('-', '');
        User u = new User(Alias = 'mrdn', Email = 'rep' + unique + '@example.com', EmailEncodingKey = 'UTF-8',
            LastName = 'Rep', LanguageLocaleKey = 'en_US', LocaleSidKey = 'en_US', ProfileId = standard.Id,
            TimeZoneSidKey = 'America/Vancouver', Username = 'rep' + unique + '@meridian.example.com');
        insert u;
        List<PermissionSetAssignment> grants = new List<PermissionSetAssignment>();
        for (PermissionSet ps : [SELECT Id FROM PermissionSet
                                 WHERE Name IN ('Crm_Platform_Meridian', 'Meridian_Guardrail_Code')]) {
            grants.add(new PermissionSetAssignment(AssigneeId = u.Id, PermissionSetId = ps.Id));
        }
        insert grants;
        return u;
    }
}
