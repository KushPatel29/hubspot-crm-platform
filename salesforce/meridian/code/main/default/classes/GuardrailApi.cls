/**
 * The guardrail as an API, for systems that price outside Salesforce (HubSpot's app functions, a quoting tool) and
 * for checking Salesforce against the other two implementations:
 *
 *   POST /services/apexrest/meridian/guardrail/v1/score
 *        {"deals": [{"key": "RFQ-1", "lines": [{"price": 100, "cost": 71.5, "quantity": 1, "targetMargin": 0.3}]}]}
 *     -> 200 {"results": [{"key": "RFQ-1", "verdict": "Below target", "approver": "Rep", "worstLine": 0, ...}]}
 *
 *   GET  /services/apexrest/meridian/guardrail/v1/opportunities/<Crm_Platform_Key__c>
 *     -> 200 the opportunity's stored guardrail fields, a fresh score of its lines, and whether the two agree
 *
 * Scoring reads nothing, so up to 500 deals of up to 200 lines each go in one call. The GET runs in user mode: a
 * caller sees only the records and fields its user may. Errors are {"errors": [{"code": ..., "path": ...}]} with a
 * 4xx status, and never echo a value from the request.
 */
@RestResource(urlMapping='/meridian/guardrail/v1/*')
global with sharing class GuardrailApi {
    public static final Integer MAX_DEALS = 500;
    public static final Integer MAX_LINES = 200;
    private static final String OPPORTUNITIES = '/meridian/guardrail/v1/opportunities/';

    public class LineInput {
        public Double price;
        public Double cost;
        public Double quantity;
        public Double targetMargin;
    }

    public class DealInput {
        public String key;
        public List<LineInput> lines;
    }

    public class ScoreRequest {
        public List<DealInput> deals;
    }

    /** A request that cannot be scored: the status, a code and where in the request. */
    private class RefusalException extends Exception {
        public Integer status;
        public String code;
        public String path;

        public RefusalException(Integer status, String code, String path) {
            this.status = status;
            this.code = code;
            this.path = path;
        }
    }

    @HttpPost
    global static void score() {
        try {
            if (!RestContext.request.requestURI.endsWith('/meridian/guardrail/v1/score')) {
                throw new RefusalException(404, 'NOT_FOUND', null);
            }
            List<Map<String, Object>> results = new List<Map<String, Object>>();
            for (DealInput deal : parse(RestContext.request.requestBody).deals) {
                results.add(view(deal.key, GuardrailService.scoreDeal(lines(deal, results.size()))));
            }
            respond(200, new Map<String, Object>{ 'results' => results });
        } catch (RefusalException refused) {
            respond(refused.status, refusal(refused));
        }
    }

    @HttpGet
    global static void opportunity() {
        try {
            String uri = RestContext.request.requestURI;
            Integer at = uri.indexOf(OPPORTUNITIES);
            String key = at < 0 ? '' : EncodingUtil.urlDecode(uri.substring(at + OPPORTUNITIES.length()), 'UTF-8');
            if (String.isBlank(key)) {
                throw new RefusalException(404, 'NOT_FOUND', null);
            }
            List<Opportunity> found = [
                SELECT Id, Meridian_Guardrail_Verdict__c, Meridian_Approver__c, Meridian_Blended_Margin__c,
                    Meridian_Margin_Gap_Dollars__c
                FROM Opportunity
                WHERE Crm_Platform_Key__c = :key
                WITH USER_MODE
                LIMIT 1
            ];
            if (found.isEmpty()) {
                throw new RefusalException(404, 'NOT_FOUND', 'key');
            }
            respond(200, compare(key, found[0]));
        } catch (RefusalException refused) {
            respond(refused.status, refusal(refused));
        }
    }

    private static ScoreRequest parse(Blob raw) {
        ScoreRequest body;
        try {
            body = raw == null ? null : (ScoreRequest) JSON.deserialize(raw.toString(), ScoreRequest.class);
        } catch (JSONException malformed) {
            throw new RefusalException(400, 'INVALID_JSON', null);
        } catch (TypeException wrongType) {
            throw new RefusalException(400, 'INVALID_JSON', null);
        }
        if (body == null || body.deals == null || body.deals.isEmpty()) {
            throw new RefusalException(400, 'NO_DEALS', 'deals');
        }
        if (body.deals.size() > MAX_DEALS) {
            throw new RefusalException(413, 'TOO_MANY_DEALS', 'deals');
        }
        return body;
    }

    private static List<GuardrailService.Line> lines(DealInput deal, Integer index) {
        String path = 'deals[' + index + ']';
        if (deal == null || deal.lines == null) {
            throw new RefusalException(400, 'MISSING_FIELD', path + '.lines');
        }
        if (deal.lines.size() > MAX_LINES) {
            throw new RefusalException(413, 'TOO_MANY_LINES', path + '.lines');
        }
        List<GuardrailService.Line> lines = new List<GuardrailService.Line>();
        for (Integer i = 0; i < deal.lines.size(); i++) {
            LineInput line = deal.lines[i];
            String at = path + '.lines[' + i + ']';
            if (line == null) {
                throw new RefusalException(400, 'MISSING_FIELD', at);
            }
            String missing = line.price == null ? 'price' : line.cost == null ? 'cost'
                : line.quantity == null ? 'quantity' : line.targetMargin == null ? 'targetMargin' : null;
            if (missing != null) {
                throw new RefusalException(400, 'MISSING_FIELD', at + '.' + missing);
            }
            lines.add(new GuardrailService.Line(line.price, line.cost, line.quantity, line.targetMargin));
        }
        return lines;
    }

    /** The stored fields beside a fresh score of the lines the caller can see, and whether they agree. */
    private static Map<String, Object> compare(String key, Opportunity opp) {
        List<GuardrailService.Line> lines = new List<GuardrailService.Line>();
        Integer unscored = 0;
        for (OpportunityLineItem item : [
            SELECT UnitPrice, Quantity, Unit_Cost__c, Product2.Meridian_Target_Margin__c
            FROM OpportunityLineItem
            WHERE OpportunityId = :opp.Id
            WITH USER_MODE
            ORDER BY SortOrder, CreatedDate, Id
        ]) {
            if (OpportunityGuardrail.isScorable(item)) {
                lines.add(OpportunityGuardrail.toLine(item));
            } else {
                unscored++;
            }
        }
        GuardrailService.DealScore live = GuardrailService.scoreDeal(lines);
        Boolean inStep = lines.isEmpty()
            ? opp.Meridian_Guardrail_Verdict__c == null
            : GuardrailService.optionValue(live.verdict) == opp.Meridian_Guardrail_Verdict__c
                && GuardrailService.optionValue(live.approver) == opp.Meridian_Approver__c;
        return new Map<String, Object>{
            'key' => key,
            'stored' => new Map<String, Object>{
                'verdict' => opp.Meridian_Guardrail_Verdict__c,
                'approver' => opp.Meridian_Approver__c,
                'blendedMarginPct' => opp.Meridian_Blended_Margin__c,
                'gapDollars' => opp.Meridian_Margin_Gap_Dollars__c
            },
            'live' => view(key, live),
            'unscoredLines' => unscored,
            'inStep' => inStep
        };
    }

    private static Map<String, Object> view(String key, GuardrailService.DealScore score) {
        List<String> verdicts = new List<String>();
        List<String> approvers = new List<String>();
        for (GuardrailService.LineScore line : score.lines) {
            verdicts.add(line.verdict);
            approvers.add(line.approver);
        }
        return new Map<String, Object>{
            'key' => key,
            'verdict' => score.verdict,
            'approver' => score.approver,
            'worstLine' => score.worstLine,
            'blendedMarginPct' => score.blendedMarginPct,
            'gapDollars' => score.gapDollars,
            'lineVerdicts' => verdicts,
            'lineApprovers' => approvers
        };
    }

    private static Map<String, Object> refusal(RefusalException refused) {
        Map<String, Object> error = new Map<String, Object>{ 'code' => refused.code };
        if (refused.path != null) {
            error.put('path', refused.path);
        }
        return new Map<String, Object>{ 'errors' => new List<Object>{ error } };
    }

    private static void respond(Integer status, Object body) {
        RestResponse response = RestContext.response;
        response.statusCode = status;
        response.addHeader('Content-Type', 'application/json');
        response.responseBody = Blob.valueOf(JSON.serialize(body));
    }
}
