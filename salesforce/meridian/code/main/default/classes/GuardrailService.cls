/**
 * Meridian's deal guardrail, in Apex: the same rule as crm_platform/guardrails.py (the loader) and
 * hubspot/shared/guardrail.js (HubSpot). GuardrailServiceTest runs it over every Meridian deal in the GuardrailParity
 * static resource, which the Python guardrail generated, and requires the same answers.
 *
 * A product's band comes from its target margin: floor max(0.05, target - 0.09), stretch target + 0.08. A line's
 * verdict is where its margin sits in that band. Approval is tiered on the gap to target, not on the discount: up to 2
 * points a rep signs, 5 a sales manager, 10 a commercial director, anything more the VP Finance. A deal is only as
 * clean as its worst line, and needs the most senior signature any line needs.
 *
 * Doubles throughout, not Decimals: the rule is defined on IEEE doubles in Python and JavaScript, and the parity
 * test compares to 1e-9.
 *
 * The band's numbers and the tiers' limits are configuration: the Meridian_Guardrail_Setting__mdt record named
 * Default. The values below are the fallback when that record is missing, and the values it ships with; a Python
 * test holds the committed record to crm_platform/guardrails.py.
 */
public inherited sharing class GuardrailService {
    public static final Double TOLERANCE = 0.000000001;
    // A Double zero: a bare 0.0 literal is a Decimal in Apex, which would turn the arithmetic below into Decimals.
    private static final Double ZERO = 0.0;
    public static final List<String> VERDICTS = new List<String>{
        'Above stretch', 'At target', 'Below target', 'Below floor', 'Loss-making'
    };
    public static final List<String> APPROVERS = new List<String>{
        'None', 'Rep', 'Sales manager', 'Commercial director', 'VP Finance'
    };

    public class Settings {
        public Double floorMinimum = 0.05;
        public Double floorDrop = 0.09;
        public Double stretchRise = 0.08;
        // Rep, sales manager, commercial director; a gap beyond the last needs the VP Finance.
        public List<Double> tierLimits = new List<Double>{ 0.02, 0.05, 0.10 };
    }

    @TestVisible
    private static Settings current;

    /** The rule's numbers, read once per transaction (getInstance does not count against the SOQL limit). */
    public static Settings settings() {
        if (current == null) {
            current = new Settings();
            Meridian_Guardrail_Setting__mdt row = Meridian_Guardrail_Setting__mdt.getInstance('Default');
            if (row != null) {
                current.floorMinimum = row.Floor_Minimum__c.doubleValue();
                current.floorDrop = row.Floor_Drop__c.doubleValue();
                current.stretchRise = row.Stretch_Rise__c.doubleValue();
                current.tierLimits = new List<Double>{
                    row.Rep_Limit__c.doubleValue(), row.Manager_Limit__c.doubleValue(),
                    row.Director_Limit__c.doubleValue()
                };
            }
        }
        return current;
    }

    public class Line {
        public Double price;
        public Double cost;
        public Double quantity;
        public Double targetMargin;

        public Line(Double price, Double cost, Double quantity, Double targetMargin) {
            this.price = price;
            this.cost = cost;
            this.quantity = quantity;
            this.targetMargin = targetMargin;
        }
    }

    public class LineScore {
        @AuraEnabled public Double marginPct;
        @AuraEnabled public String verdict;
        @AuraEnabled public String approver;
        @AuraEnabled public Double gap;
        @AuraEnabled public Double gapDollars;
        @AuraEnabled public Double floor;
        @AuraEnabled public Double target;
        @AuraEnabled public Double stretch;
        public Double revenue;
        public Double margin;
    }

    public class DealScore {
        @AuraEnabled public Double blendedMarginPct;
        @AuraEnabled public String verdict;
        @AuraEnabled public String approver;
        @AuraEnabled public Double gapDollars;
        @AuraEnabled public Integer worstLine;
        @AuraEnabled public List<LineScore> lines;
    }

    public static LineScore scoreLine(Double price, Double cost, Double quantity, Double targetMargin) {
        Settings rule = settings();
        LineScore s = new LineScore();
        s.floor = Math.max(rule.floorMinimum, targetMargin - rule.floorDrop);
        s.target = targetMargin;
        s.stretch = targetMargin + rule.stretchRise;
        s.revenue = price * quantity;
        s.marginPct = price > 0 ? (price - cost) / price : ZERO;
        if (s.marginPct >= s.stretch - TOLERANCE) {
            s.verdict = 'Above stretch';
        } else if (s.marginPct >= s.target - TOLERANCE) {
            s.verdict = 'At target';
        } else if (s.marginPct >= s.floor - TOLERANCE) {
            s.verdict = 'Below target';
        } else if (price - cost > 0) {
            s.verdict = 'Below floor';
        } else {
            s.verdict = 'Loss-making';
        }
        s.gap = s.target - s.marginPct;
        s.approver = 'None';
        if (s.gap > TOLERANCE) {
            s.approver = 'VP Finance';
            for (Integer i = 0; i < rule.tierLimits.size(); i++) {
                if (s.gap <= rule.tierLimits[i]) {
                    s.approver = APPROVERS[i + 1];
                    break;
                }
            }
        }
        s.gapDollars = Math.max(ZERO, s.gap) * s.revenue;
        s.margin = (price - cost) * quantity;
        return s;
    }

    public static DealScore scoreDeal(List<Line> lines) {
        DealScore deal = new DealScore();
        deal.lines = new List<LineScore>();
        for (Line line : lines) {
            deal.lines.add(scoreLine(line.price, line.cost, line.quantity, line.targetMargin));
        }
        if (deal.lines.isEmpty()) {
            deal.blendedMarginPct = ZERO;
            deal.verdict = 'At target';
            deal.approver = 'None';
            deal.gapDollars = ZERO;
            deal.worstLine = -1;
            return deal;
        }
        Double revenue = ZERO;
        Double margin = ZERO;
        Double gapDollars = ZERO;
        Integer worst = 0;
        Integer approverRank = 0;
        for (Integer i = 0; i < deal.lines.size(); i++) {
            LineScore s = deal.lines[i];
            revenue += s.revenue;
            margin += s.margin;
            gapDollars += s.gapDollars;
            // Worst verdict first, then the largest gap; an exact tie keeps the earlier line.
            LineScore w = deal.lines[worst];
            Integer rank = VERDICTS.indexOf(s.verdict);
            Integer worstRank = VERDICTS.indexOf(w.verdict);
            if (rank > worstRank || (rank == worstRank && s.gap > w.gap)) {
                worst = i;
            }
            approverRank = Math.max(approverRank, APPROVERS.indexOf(s.approver));
        }
        deal.blendedMarginPct = revenue > 0 ? margin / revenue : ZERO;
        deal.verdict = deal.lines[worst].verdict;
        deal.approver = APPROVERS[approverRank];
        deal.gapDollars = gapDollars;
        deal.worstLine = worst;
        return deal;
    }

    /** "Below floor" -> "below_floor": the restricted picklist values the model generates. */
    public static String optionValue(String label) {
        return label == null ? null : label.toLowerCase().replaceAll('[^a-z0-9]+', '_').removeStart('_').removeEnd('_');
    }
}
