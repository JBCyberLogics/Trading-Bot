import com.dukascopy.api.Configurable;
import com.dukascopy.api.IAccount;
import com.dukascopy.api.IBar;
import com.dukascopy.api.IConsole;
import com.dukascopy.api.IContext;
import com.dukascopy.api.IMessage;
import com.dukascopy.api.IStrategy;
import com.dukascopy.api.ITick;
import com.dukascopy.api.Instrument;
import com.dukascopy.api.JFException;
import com.dukascopy.api.OfferSide;
import com.dukascopy.api.Period;

import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.Locale;

/**
 * JForex strategy port of the Pine indicator:
 * "Chart Patterns [Mr.Wanjama]".
 *
 * This strategy does pattern detection only (no order placement).
 */
public class ProgramJForex implements IStrategy {

    public enum PatternVisibility {
        ALL,
        ACTIVE,
        CONFIRMED,
        INVALID
    }

    private enum PatternStatus {
        ACTIVE,
        CONFIRMED,
        INVALID
    }

    private enum PatternType {
        LL,
        LL_LH,
        HH,
        HH_HL,
        DOUBLE_TOP,
        DOUBLE_BOTTOM
    }

    private static final class PatternInfo {
        PatternType patternType;
        PatternStatus status;
        Integer breakBarIdx;

        int p1Idx;
        int p2Idx;
        int p3Idx;
        int p4Idx;
        int p5Idx;
        int p6Idx;

        double p1Price;
        double p2Price;
        double p3Price;
        double p4Price;
        double p5Price;
        double p6Price;

        Double supportSlope;
        Double resistanceSlope;

        Integer firstConnectingLineBreakIdx;
        Integer lastConnectingLineBreakIdx;

        PatternInfo(PatternType patternType, PatternStatus status) {
            this.patternType = patternType;
            this.status = status;
        }

        String patternLabel() {
            switch (patternType) {
                case LL:
                    return "LL";
                case LL_LH:
                    return "LL & LH";
                case HH:
                    return "HH";
                case HH_HL:
                    return "HH & HL";
                case DOUBLE_TOP:
                    return "Double Top";
                case DOUBLE_BOTTOM:
                    return "Double Bottom";
                default:
                    return patternType.name();
            }
        }
    }

    private static final class PivotResult {
        Double ph;
        Integer phx;
        Double pl;
        Integer plx;
        boolean newPivotDetected;
    }

    @Configurable("Instrument")
    public Instrument instrument = Instrument.EURUSD;

    @Configurable("Period")
    public Period period = Period.THIRTY_MINS;

    @Configurable("Offer Side")
    public OfferSide offerSide = OfferSide.BID;

    @Configurable("Show Last History (Bars)")
    public int showLast = 3000;

    @Configurable("Pattern Visibility")
    public PatternVisibility patternsVisibility = PatternVisibility.ALL;

    @Configurable("Pivot Period")
    public int pivotPeriod = 10;

    @Configurable("ATR Length")
    public int atrLength = 14;

    @Configurable("Lower Low")
    public boolean showLowerLow = true;

    @Configurable("Lower Low & Lower High")
    public boolean showLowerLowLowerHigh = true;

    @Configurable("Higher High")
    public boolean showHigherHigh = true;

    @Configurable("Higher High & Higher Low")
    public boolean showHigherHighHigherLow = true;

    @Configurable("Double Tops")
    public boolean showDoubleTops = true;

    @Configurable("Double Bottoms")
    public boolean showDoubleBottoms = true;

    private IConsole console;

    private final List<Long> times = new ArrayList<Long>();
    private final List<Double> highs = new ArrayList<Double>();
    private final List<Double> lows = new ArrayList<Double>();
    private final List<Double> closes = new ArrayList<Double>();

    private final List<Double> trSeries = new ArrayList<Double>();
    private final List<Double> atrSeries = new ArrayList<Double>();

    private final List<Double> pivotsPrices = new ArrayList<Double>();
    private final List<Integer> pivotsBarIndices = new ArrayList<Integer>();
    private final List<PatternInfo> activePatterns = new ArrayList<PatternInfo>();

    private int barIndex = -1;
    private int lastPivotState = 1;

    @Override
    public void onStart(IContext context) throws JFException {
        this.console = context.getConsole();
        context.setSubscribedInstruments(java.util.Collections.singleton(instrument), true);
        log("ProgramJForex started for " + instrument + " " + period + " (offerSide=" + offerSide + ")");
    }

    @Override
    public void onAccount(IAccount account) throws JFException {
        // No-op
    }

    @Override
    public void onMessage(IMessage message) throws JFException {
        // No-op
    }

    @Override
    public void onTick(Instrument instrument, ITick tick) throws JFException {
        // No-op (pattern logic runs on completed bars in onBar)
    }

    @Override
    public void onBar(Instrument instrument, Period period, IBar askBar, IBar bidBar) throws JFException {
        if (!this.instrument.equals(instrument) || !this.period.equals(period)) {
            return;
        }

        IBar bar = offerSide == OfferSide.ASK ? askBar : bidBar;
        appendBar(bar);

        PivotResult pivotResult = getPivotPoints(pivotPeriod);

        if (pivotResult.newPivotDetected && pivotsPrices.size() >= 6) {
            detectPatternsOnNewPivot();
        }

        updateActivePatterns(pivotResult.newPivotDetected);
        cleanupPatterns();
    }

    @Override
    public void onStop() throws JFException {
        log("ProgramJForex stopped. Total bars processed: " + (barIndex + 1));
    }

    private void appendBar(IBar bar) {
        barIndex++;
        times.add(bar.getTime());
        highs.add(bar.getHigh());
        lows.add(bar.getLow());
        closes.add(bar.getClose());

        double tr = computeTrueRange(barIndex);
        trSeries.add(tr);
        atrSeries.add(computeAtrAt(barIndex));
    }

    private double computeTrueRange(int index) {
        double high = highs.get(index);
        double low = lows.get(index);

        if (index == 0) {
            return high - low;
        }

        double prevClose = closes.get(index - 1);
        double a = high - low;
        double b = Math.abs(high - prevClose);
        double c = Math.abs(low - prevClose);

        return Math.max(a, Math.max(b, c));
    }

    private double computeAtrAt(int index) {
        if (index < atrLength - 1) {
            return Double.NaN;
        }

        if (index == atrLength - 1) {
            double sum = 0.0;
            for (int i = 0; i < atrLength; i++) {
                sum += trSeries.get(i);
            }
            return sum / atrLength;
        }

        double prevAtr = atrSeries.get(index - 1);
        if (Double.isNaN(prevAtr)) {
            return Double.NaN;
        }

        double tr = trSeries.get(index);
        return ((prevAtr * (atrLength - 1)) + tr) / atrLength;
    }

    private PivotResult getPivotPoints(int period) {
        PivotResult result = new PivotResult();
        result.newPivotDetected = false;

        if (barIndex < period) {
            return result;
        }

        int candidateIdx = barIndex - period;

        double upper = highestHigh(barIndex - period + 1, barIndex);
        double lower = lowestLow(barIndex - period + 1, barIndex);

        int previousPivotState = lastPivotState;
        double candidateHigh = highs.get(candidateIdx);
        double candidateLow = lows.get(candidateIdx);

        if (candidateHigh > upper) {
            lastPivotState = 1;
        } else if (candidateLow < lower) {
            lastPivotState = -1;
        }

        if (lastPivotState == 1 && previousPivotState != 1) {
            result.ph = candidateHigh;
            result.phx = candidateIdx;
            pivotsPrices.add(result.ph);
            pivotsBarIndices.add(result.phx);
            result.newPivotDetected = true;
        }

        if (lastPivotState == -1 && previousPivotState != -1) {
            result.pl = candidateLow;
            result.plx = candidateIdx;
            pivotsPrices.add(result.pl);
            pivotsBarIndices.add(result.plx);
            result.newPivotDetected = true;
        }

        return result;
    }

    private double highestHigh(int from, int to) {
        int start = Math.max(0, from);
        int end = Math.min(barIndex, to);
        double max = Double.NEGATIVE_INFINITY;
        for (int i = start; i <= end; i++) {
            max = Math.max(max, highs.get(i));
        }
        return max;
    }

    private double lowestLow(int from, int to) {
        int start = Math.max(0, from);
        int end = Math.min(barIndex, to);
        double min = Double.POSITIVE_INFINITY;
        for (int i = start; i <= end; i++) {
            min = Math.min(min, lows.get(i));
        }
        return min;
    }

    private void detectPatternsOnNewPivot() {
        int n = pivotsPrices.size();

        int i1 = pivotsBarIndices.get(n - 6);
        int i2 = pivotsBarIndices.get(n - 5);
        int i3 = pivotsBarIndices.get(n - 4);
        int i4 = pivotsBarIndices.get(n - 3);
        int i5 = pivotsBarIndices.get(n - 2);
        int i6 = pivotsBarIndices.get(n - 1);

        double p1 = pivotsPrices.get(n - 6);
        double p2 = pivotsPrices.get(n - 5);
        double p3 = pivotsPrices.get(n - 4);
        double p4 = pivotsPrices.get(n - 3);
        double p5 = pivotsPrices.get(n - 2);
        double p6 = pivotsPrices.get(n - 1);

        if (showLowerLow && lowerLow(p2, p3, p4, p5, p6)) {
            PatternInfo pattern = basePattern(PatternType.LL, PatternStatus.CONFIRMED,
                    i1, i2, i3, i4, i5, i6,
                    p1, p2, p3, p4, p5, p6);
            activePatterns.add(pattern);
            logPattern(pattern, "Detected");
        }

        if (showLowerLowLowerHigh && lowerLowLowerHigh(p1, p2, p3, p4, p5, p6)) {
            PatternInfo pattern = basePattern(PatternType.LL_LH, PatternStatus.CONFIRMED,
                    i1, i2, i3, i4, i5, i6,
                    p1, p2, p3, p4, p5, p6);
            activePatterns.add(pattern);
            logPattern(pattern, "Detected");
        }

        if (showHigherHigh && higherHigh(p2, p3, p4, p5, p6)) {
            PatternInfo pattern = basePattern(PatternType.HH, PatternStatus.CONFIRMED,
                    i1, i2, i3, i4, i5, i6,
                    p1, p2, p3, p4, p5, p6);
            activePatterns.add(pattern);
            logPattern(pattern, "Detected");
        }

        if (showHigherHighHigherLow && higherHighHigherLow(p1, p2, p3, p4, p5, p6)) {
            PatternInfo pattern = basePattern(PatternType.HH_HL, PatternStatus.CONFIRMED,
                    i1, i2, i3, i4, i5, i6,
                    p1, p2, p3, p4, p5, p6);
            activePatterns.add(pattern);
            logPattern(pattern, "Detected");
        }

        if (showDoubleTops && doubleTop(p4, p5, p6, i3, i6)) {
            PatternInfo pattern = basePattern(PatternType.DOUBLE_TOP, PatternStatus.ACTIVE,
                    i1, i2, i3, i4, i5, i6,
                    p1, p2, p3, p4, p5, p6);

            fillDoublePatternInternalState(pattern);
            activePatterns.add(pattern);
            logPattern(pattern, "Detected");
        }

        if (showDoubleBottoms && doubleBottom(p4, p5, p6, i3, i6)) {
            PatternInfo pattern = basePattern(PatternType.DOUBLE_BOTTOM, PatternStatus.ACTIVE,
                    i1, i2, i3, i4, i5, i6,
                    p1, p2, p3, p4, p5, p6);

            fillDoublePatternInternalState(pattern);
            activePatterns.add(pattern);
            logPattern(pattern, "Detected");
        }
    }

    private PatternInfo basePattern(
            PatternType type,
            PatternStatus status,
            int i1, int i2, int i3, int i4, int i5, int i6,
            double p1, double p2, double p3, double p4, double p5, double p6
    ) {
        PatternInfo p = new PatternInfo(type, status);
        p.p1Idx = i1;
        p.p2Idx = i2;
        p.p3Idx = i3;
        p.p4Idx = i4;
        p.p5Idx = i5;
        p.p6Idx = i6;

        p.p1Price = p1;
        p.p2Price = p2;
        p.p3Price = p3;
        p.p4Price = p4;
        p.p5Price = p5;
        p.p6Price = p6;

        p.supportSlope = getPivotsSlope(p.p3Price, p.p4Price, p.p3Idx, p.p4Idx);
        p.resistanceSlope = p.supportSlope;

        return p;
    }

    private void fillDoublePatternInternalState(PatternInfo pattern) {
        for (int i = pattern.p3Idx; i <= pattern.p4Idx; i++) {
            Double slope = getPivotsSlope(pattern.p3Price, pattern.p4Price, pattern.p3Idx, pattern.p4Idx);
            if (slope == null) {
                break;
            }

            double projected = slope * (i - pattern.p3Idx) + pattern.p3Price;
            if (pattern.patternType == PatternType.DOUBLE_TOP && projected >= pattern.p5Price) {
                pattern.firstConnectingLineBreakIdx = i;
                break;
            }
            if (pattern.patternType == PatternType.DOUBLE_BOTTOM && projected <= pattern.p5Price) {
                pattern.firstConnectingLineBreakIdx = i;
                break;
            }
        }

        for (int i = pattern.p6Idx; i <= barIndex; i++) {
            double close = closes.get(i);
            if (pattern.patternType == PatternType.DOUBLE_TOP && close < pattern.p5Price) {
                pattern.lastConnectingLineBreakIdx = i;
                pattern.breakBarIdx = i;
                pattern.status = PatternStatus.CONFIRMED;
                break;
            }
            if (pattern.patternType == PatternType.DOUBLE_BOTTOM && close > pattern.p5Price) {
                pattern.lastConnectingLineBreakIdx = i;
                pattern.breakBarIdx = i;
                pattern.status = PatternStatus.CONFIRMED;
                break;
            }
        }
    }

    private void updateActivePatterns(boolean newPivotDetected) {
        for (PatternInfo pattern : activePatterns) {
            if (pattern.status == PatternStatus.ACTIVE) {
                updatePattern(pattern);
            }

            if (newPivotDetected
                    && pattern.status == PatternStatus.ACTIVE
                    && pattern.p6Idx < barIndex - pivotPeriod) {
                int pivotCheckIndex = barIndex - pivotPeriod;
                if (pivotCheckIndex >= 0 && pivotCheckIndex < lows.size()) {
                    double pivotLow = lows.get(pivotCheckIndex);
                    boolean invalidateTop = pattern.patternType == PatternType.DOUBLE_TOP && pivotLow >= pattern.p5Price;
                    boolean invalidateBottom = pattern.patternType == PatternType.DOUBLE_BOTTOM && pivotLow <= pattern.p5Price;
                    if (invalidateTop || invalidateBottom) {
                        pattern.status = PatternStatus.INVALID;
                        pattern.breakBarIdx = pivotCheckIndex;
                        logPattern(pattern, "Invalidated");
                    }
                }
            }
        }
    }

    private void updatePattern(PatternInfo pattern) {
        if (barIndex < 0) {
            return;
        }

        double close = closes.get(barIndex);

        if (pattern.patternType == PatternType.DOUBLE_TOP && close < pattern.p5Price) {
            pattern.lastConnectingLineBreakIdx = barIndex;
            pattern.breakBarIdx = barIndex;
            pattern.status = PatternStatus.CONFIRMED;
            logPattern(pattern, "Confirmed");
        } else if (pattern.patternType == PatternType.DOUBLE_BOTTOM && close > pattern.p5Price) {
            pattern.lastConnectingLineBreakIdx = barIndex;
            pattern.breakBarIdx = barIndex;
            pattern.status = PatternStatus.CONFIRMED;
            logPattern(pattern, "Confirmed");
        }
    }

    private void cleanupPatterns() {
        Iterator<PatternInfo> iterator = activePatterns.iterator();
        while (iterator.hasNext()) {
            PatternInfo pattern = iterator.next();
            if (pattern.p1Idx < barIndex - showLast) {
                iterator.remove();
            }
        }
    }

    private void logPattern(PatternInfo pattern, String action) {
        if (!isVisible(pattern.status)) {
            return;
        }

        int x = pattern.breakBarIdx != null ? pattern.breakBarIdx : pattern.p6Idx;
        long t = (x >= 0 && x < times.size()) ? times.get(x) : 0L;

        log(String.format(Locale.US,
                "%s %s | status=%s | p5=%.5f | barIndex=%d | time=%d",
                action,
                pattern.patternLabel(),
                pattern.status,
                pattern.p5Price,
                x,
                t));
    }

    private boolean isVisible(PatternStatus status) {
        switch (patternsVisibility) {
            case ACTIVE:
                return status == PatternStatus.ACTIVE;
            case CONFIRMED:
                return status == PatternStatus.CONFIRMED;
            case INVALID:
                return status == PatternStatus.INVALID;
            case ALL:
            default:
                return true;
        }
    }

    private Double getPivotsSlope(double p1Price, double p2Price, int p1Index, int p2Index) {
        if (Double.isNaN(p1Price) || Double.isNaN(p2Price) || p1Index == p2Index) {
            return null;
        }
        return (p2Price - p1Price) / (double) (p2Index - p1Index);
    }

    private boolean lowerLow(double p1, double p2, double p3, double p4, double p5) {
        return p1 < p2 && p1 < p3
                && p2 > p3 && p2 < p4
                && p3 < p4
                && p4 > p5
                && p5 < p3;
    }

    private boolean lowerLowLowerHigh(double p1, double p2, double p3, double p4, double p5, double p6) {
        return p1 < p2 && p1 < p3
                && p2 > p3 && p2 < p4
                && p3 < p4
                && p4 > p5 && p4 > p6
                && p5 < p3 && p5 < p6;
    }

    private boolean higherHigh(double p1, double p2, double p3, double p4, double p5) {
        return p1 > p2 && p1 > p3
                && p2 < p3 && p2 > p4
                && p3 > p4
                && p4 < p5
                && p5 > p3;
    }

    private boolean higherHighHigherLow(double p1, double p2, double p3, double p4, double p5, double p6) {
        return p1 > p2 && p1 > p3
                && p2 < p3 && p2 > p4
                && p3 > p4
                && p4 < p5 && p4 < p6
                && p5 > p3 && p5 > p6;
    }

    private boolean doubleTop(double p2, double p3, double p4, int p1Idx, int p4Idx) {
        if (!isIndexValid(p1Idx) || !isIndexValid(p4Idx)) {
            return false;
        }

        double closeAtP1 = closes.get(p1Idx);
        double atrAtP4 = atrSeries.get(p4Idx);

        if (Double.isNaN(atrAtP4)) {
            return false;
        }

        return closeAtP1 < p3
                && p2 > p3
                && p4 > p3
                && Math.abs(p2 - p4) <= atrAtP4 / 6.0;
    }

    private boolean doubleBottom(double p2, double p3, double p4, int p1Idx, int p4Idx) {
        if (!isIndexValid(p1Idx) || !isIndexValid(p4Idx)) {
            return false;
        }

        double closeAtP1 = closes.get(p1Idx);
        double atrAtP4 = atrSeries.get(p4Idx);

        if (Double.isNaN(atrAtP4)) {
            return false;
        }

        return closeAtP1 > p3
                && p2 < p3
                && p4 < p3
                && Math.abs(p2 - p4) <= atrAtP4 / 6.0;
    }

    private boolean isIndexValid(int idx) {
        return idx >= 0 && idx < closes.size() && idx < atrSeries.size();
    }

    private void log(String message) {
        if (console != null) {
            console.getOut().println(message);
        }
    }
}
