/**
 * The guardrail's REST API: scoring many deals in one call exactly as GuardrailService does, refusing what it cannot
 * score with a code and a path (never a value), and reading an opportunity by its key in user mode.
 */
@IsTest
private class GuardrailApiTest {
    private static Map<String, Object> call(String method, String uri, String body) {
        RestRequest request = new RestRequest();
        request.httpMethod = method;
        request.requestURI = uri;
        if (body != null) {
            request.requestBody = Blob.valueOf(body);
        }
        RestContext.request = request;
        RestContext.response = new RestResponse();
        if (method == 'POST') {
            GuardrailApi.score();
        } else {
            GuardrailApi.opportunity();
        }
        Map<String, Object> out = (Map<String, Object>) JSON.deserializeUntyped(
            RestContext.response.responseBody.toString());
        out.put('status', RestContext.response.statusCode);
        return out;
    }

    private static Map<String, Object> firstError(Map<String, Object> out) {
        return (Map<String, Object>) ((List<Object>) out.get('errors'))[0];
    }

    private static String line(Object price, Object cost, Object quantity, Object target) {
        return JSON.serialize(new Map<String, Object>{
            'price' => price, 'cost' => cost, 'quantity' => quantity, 'targetMargin' => target
        });
    }

    @IsTest
    static void scoresManyDealsInOneCallAsTheServiceDoes() {
        String body = '{"deals": ['
            + '{"key": "RFQ-1", "lines": [' + line(100, 60, 1, 0.3) + ',' + line(100, 75, 1, 0.3) + ',' + line(100, 71, 5, 0.3) + ']},'
            + '{"key": "RFQ-2", "lines": []}'
            + ']}';
        Map<String, Object> out = call('POST', '/meridian/guardrail/v1/score', body);
        Assert.areEqual(200, out.get('status'));
        List<Object> results = (List<Object>) out.get('results');
        Assert.areEqual(2, results.size());
        Map<String, Object> first = (Map<String, Object>) results[0];
        Assert.areEqual('RFQ-1', first.get('key'));
        Assert.areEqual('Below target', first.get('verdict'));
        Assert.areEqual('Sales manager', first.get('approver'));
        Assert.areEqual(1, first.get('worstLine'));
        Assert.isTrue(Math.abs(((Decimal) first.get('blendedMarginPct')).doubleValue() - 0.3) < 0.000000001);
        Assert.areEqual(new List<Object>{ 'Above stretch', 'Below target', 'Below target' }, first.get('lineVerdicts'));
        Map<String, Object> empty = (Map<String, Object>) results[1];
        Assert.areEqual('None', empty.get('approver'));
        Assert.areEqual(-1, empty.get('worstLine'));
    }

    @IsTest
    static void refusesWhatItCannotScoreWithACodeAndAPath() {
        Map<String, Object> out = call('POST', '/meridian/guardrail/v1/score', '{"deals": [');
        Assert.areEqual(400, out.get('status'));
        Assert.areEqual('INVALID_JSON', firstError(out).get('code'));

        out = call('POST', '/meridian/guardrail/v1/score', '{"deals": [{"lines": [{"price": "cheap"}]}]}');
        Assert.areEqual(400, out.get('status'));
        Assert.areEqual('INVALID_JSON', firstError(out).get('code'));
        Assert.isFalse(JSON.serialize(out).contains('cheap'), 'an error never echoes a value');

        out = call('POST', '/meridian/guardrail/v1/score', '{"deals": []}');
        Assert.areEqual('NO_DEALS', firstError(out).get('code'));

        out = call('POST', '/meridian/guardrail/v1/score', null);
        Assert.areEqual('NO_DEALS', firstError(out).get('code'));

        out = call('POST', '/meridian/guardrail/v1/score', '{"deals": [{"key": "A"}]}');
        Assert.areEqual('deals[0].lines', firstError(out).get('path'));

        out = call('POST', '/meridian/guardrail/v1/score',
            '{"deals": [{"key": "A", "lines": [' + line(100, 60, 1, 0.3) + ']}, {"key": "B", "lines": [' + line(100, null, 1, 0.3) + ']}]}');
        Assert.areEqual(400, out.get('status'));
        Assert.areEqual('MISSING_FIELD', firstError(out).get('code'));
        Assert.areEqual('deals[1].lines[0].cost', firstError(out).get('path'));

        List<String> deals = new List<String>();
        for (Integer i = 0; i <= GuardrailApi.MAX_DEALS; i++) {
            deals.add('{"lines": []}');
        }
        out = call('POST', '/meridian/guardrail/v1/score', '{"deals": [' + String.join(deals, ',') + ']}');
        Assert.areEqual(413, out.get('status'));
        Assert.areEqual('TOO_MANY_DEALS', firstError(out).get('code'));

        List<String> lines = new List<String>();
        for (Integer i = 0; i <= GuardrailApi.MAX_LINES; i++) {
            lines.add(line(100, 60, 1, 0.3));
        }
        out = call('POST', '/meridian/guardrail/v1/score', '{"deals": [{"lines": [' + String.join(lines, ',') + ']}]}');
        Assert.areEqual('TOO_MANY_LINES', firstError(out).get('code'));

        out = call('POST', '/meridian/guardrail/v1/elsewhere', '{"deals": [{"lines": []}]}');
        Assert.areEqual(404, out.get('status'));
    }

    @IsTest
    static void readsAnOpportunityByItsKeyAndSaysWhetherItIsInStep() {
        PricebookEntry kettle = GuardrailTestData.product('Kettle', 0.30);
        Opportunity opp = GuardrailTestData.deal('Keyed', 'RFQ-API-1', kettle, 100, 71.5);
        User rep = GuardrailTestData.salesUser();
        Map<String, Object> out;
        System.runAs(rep) {
            out = call('GET', '/meridian/guardrail/v1/opportunities/RFQ-API-1', null);
        }
        Assert.areEqual(200, out.get('status'), JSON.serialize(out));
        Assert.areEqual(true, out.get('inStep'));
        Assert.areEqual('below_target', ((Map<String, Object>) out.get('stored')).get('verdict'));
        Assert.areEqual('Below target', ((Map<String, Object>) out.get('live')).get('verdict'));
        Assert.areEqual(0, out.get('unscoredLines'));

        update new Opportunity(Id = opp.Id, Meridian_Guardrail_Verdict__c = 'above_stretch');
        System.runAs(rep) {
            out = call('GET', '/meridian/guardrail/v1/opportunities/RFQ-API-1', null);
        }
        Assert.areEqual(false, out.get('inStep'), 'a verdict edited by hand is reported, not hidden');
    }

    @IsTest
    static void anUnknownKeyIsNotFound() {
        Map<String, Object> out = call('GET', '/meridian/guardrail/v1/opportunities/RFQ-NOWHERE', null);
        Assert.areEqual(404, out.get('status'));
        Assert.areEqual('key', firstError(out).get('path'));
        out = call('GET', '/meridian/guardrail/v1/opportunities/', null);
        Assert.areEqual(404, out.get('status'));
    }
}
