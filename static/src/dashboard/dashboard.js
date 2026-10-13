/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { browser } from "@web/core/browser/browser";
import { user } from "@web/core/user";

const OPP = ["type", "=", "opportunity"];

// A deal is at risk when its next step is overdue, when it has no next step at
// all, or when it has sat in one stage too long. Prefix notation: two ORs bind
// the three leaves into a single condition.
const AT_RISK = [
    "|", "|",
    ["activity_state", "=", "overdue"],
    ["activity_state", "=", false],
    ["is_rotting", "=", true],
];

// Palette re-validated in the order used here, against a white card surface
// (node scripts/validate_palette.js --mode light --surface #ffffff):
//   categorical 8   worst adjacent CVD dE 7.2, normal-vision 22.9   -> PASS
//   violet ramp     monotone L, light end 2.23:1 on white           -> PASS
//   trend trio      worst all-pairs CVD dE 9.2, normal 27.6         -> PASS
// Three slots sit under 3:1 on white (aqua 2.82, yellow 2.17, magenta 2.69) and
// the red/green adjacency lands in the 6-8 CVD band: both are legal only with
// secondary encoding, so every mark painted with them carries a dot, a name and
// a number beside it. That is the documented relief, not taste.
const ACCENT = "#6250d6";
const C = {
    open: ACCENT,      // categorical slot 1, violet - the accent of the skin
    won: "#1baf7a",    // aqua
    lost: "#eb6834",   // orange
};
// One hue, light -> dark. Ordinal: stages are a sequence, not a set of peers.
const RAMP = ["#b3a3f2", "#9076e9", "#7357dd", "#5a46cc"];
// The track a bar sits in.
const TRACK = "#eeecfa";

// Categorical slots, in the exact order the validator passed them. Used only
// where a colour means an IDENTITY - a campaign, a person - never for a
// quantity or an ordered step. Assigned by position and never cycled, so a
// campaign keeps its colour when the list is filtered.
const CAT = [ACCENT, "#eb6834", "#1baf7a", "#eda100",
             "#2a78d6", "#e34948", "#008300", "#e87ba4"];
const catColor = (i) => CAT[i % CAT.length];

// How many monthly buckets the trend chart shows.
const TREND_MONTHS = 6;

const REFRESH_MS = 30000;

// Period filter. Applied to creation date, so every figure on the page answers
// "in this window" rather than silently mixing all-time and windowed numbers.
const PERIODS = [
    { key: "7", label: "7 days", days: 7 },
    { key: "30", label: "30 days", days: 30 },
    { key: "90", label: "90 days", days: 90 },
    { key: "all", label: "All time", days: null },
];

export class SdlcCrmDashboard extends Component {
    static template = "sdlc_CRM.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.periods = PERIODS;
        this.colors = C;
        this.accent = ACCENT;
        this.track = TRACK;
        this.state = useState({
            loading: true,
            autoRefresh: true,
            period: "30",
            campaignId: null,       // null = every campaign
            campaigns: [],
            // Deals often carry no expected revenue, and a money-only dashboard
            // reads as broken when they don't. Measure is switchable, and falls
            // back to deal count on its own while nothing has a value.
            measure: "value",
            measureLocked: false,
            updatedAt: "",
            k: {},
            stages: [],
            share: [],
            priority: [],
            sentiment: {},
            owners: [],
            velocity: [],
            donut: [],
            lostReasons: [],
            topDeals: [],
            activity: [],
            trend: { labels: [], series: [], ticks: [], max: 0, hover: null },
        });
        this._timer = null;
        onWillStart(async () => {
            await this.load();
            this._startAuto();
        });
        onWillUnmount(() => this._stopAuto());
    }

    // ---- period helpers ----------------------------------------------
    get periodDays() {
        return (PERIODS.find((p) => p.key === this.state.period) || {}).days;
    }
    get periodLabel() {
        return (PERIODS.find((p) => p.key === this.state.period) || {}).label;
    }
    _since(daysAgo) {
        const d = new Date();
        d.setDate(d.getDate() - daysAgo);
        return d.toISOString().slice(0, 19).replace("T", " ");
    }
    /** Deals CREATED in the window - for "new deals" and conversion. */
    _window() {
        const days = this.periodDays;
        return days ? [["create_date", ">=", this._since(days)]] : [];
    }
    /** Deals CLOSED in the window - for won, lost and win rate.
     *
     *  The open pipeline deliberately uses neither: what is open is open today,
     *  whenever it was created. Filtering it by creation date hid older live
     *  deals and made the headline disagree with the board.
     */
    _closedWindow() {
        const days = this.periodDays;
        return days ? [["date_closed", ">=", this._since(days)]] : [];
    }
    /** Domain fragment for the selected campaign; empty when showing all. */
    _campaign() {
        return this.state.campaignId ? [["campaign_id", "=", this.state.campaignId]] : [];
    }
    get campaignName() {
        const c = this.state.campaigns.find((x) => x.id === this.state.campaignId);
        return c ? c.name : "All campaigns";
    }
    setMeasure(measure) {
        this.state.measure = measure;
        this.state.measureLocked = true;   // an explicit choice stops the fallback
    }
    get measureIsValue() {
        return this.state.measure === "value";
    }
    /** True once any deal carries a value. Until then a money figure is not a
     *  fact about the business, it is a fact about empty fields - so the tiles
     *  say that rather than printing a confident zero. */
    get hasValue() {
        const k = this.state.k;
        return !!(k.openValue || k.wonValue || k.lostValue);
    }
    /** Open deals that nobody has priced - the reason the money reads zero. */
    get unpricedCount() {
        return this.state.k.unpricedCount || 0;
    }

    /** Greeting, as the reference has it. First name only. */
    get firstName() {
        return (user.name || "there").split(" ")[0];
    }

    // ---- trend geometry --------------------------------------------------
    /** The last n calendar months, oldest first. */
    _months(n) {
        const out = [];
        const d = new Date();
        d.setDate(1);
        d.setMonth(d.getMonth() - (n - 1));
        for (let i = 0; i < n; i++) {
            out.push({
                key: d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0"),
                label: d.toLocaleDateString(undefined, { month: "short" }),
            });
            d.setMonth(d.getMonth() + 1);
        }
        return out;
    }
    _monthsAgoISO(n) {
        const d = new Date();
        d.setDate(1);
        d.setMonth(d.getMonth() - (n - 1));
        return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-01 00:00:00";
    }
    /** A date groupby comes back as (start, label); bucket on the start. */
    _bucket(groups, field, months) {
        const by = {};
        for (const g of groups) {
            const raw = g[field];
            if (!raw) {
                continue;
            }
            const key = String(Array.isArray(raw) ? raw[0] : raw).slice(0, 7);
            by[key] = (by[key] || 0) + (g.__count || 0);
        }
        return months.map((m) => by[m.key] || 0);
    }
    /** Lines, areas and dots on a shared axis. ONE axis: every series here is a
     *  deal count, so they are directly comparable and never need a second. */
    _lines(months, series) {
        const W = 600, H = 275, L = 30, R = 10, T = 14, B = 36;
        const n = months.length;
        const raw = Math.max(1, ...series.map((sr) => Math.max(...sr.values)));
        const step = Math.max(1, Math.ceil(raw / 4));
        const max = step * 4;
        const x = (i) => (n < 2 ? L + (W - L - R) / 2 : L + (i * (W - L - R)) / (n - 1));
        const y = (v) => T + (1 - v / max) * (H - T - B);
        const built = series.map((sr) => {
            const pts = sr.values.map((v, i) => ({ x: x(i), y: y(v), v }));
            const line = pts.map((pt, i) => (i ? "L" : "M") + pt.x + " " + pt.y).join(" ");
            return {
                ...sr,
                points: pts,
                path: line,
                area: line + " L" + x(n - 1) + " " + y(0) + " L" + x(0) + " " + y(0) + " Z",
            };
        });
        return {
            labels: months.map((m, i) => ({ text: m.label, x: x(i) })),
            series: built,
            ticks: [0, 1, 2, 3, 4].map((i) => ({ v: step * i, y: y(step * i) })),
            max,
            geom: { W, H, L, R, T, B },
            hover: null,
        };
    }
    /** Crosshair: snap to the nearest month under the pointer. */
    onTrendMove(ev) {
        const t = this.state.trend;
        if (!t.labels.length) {
            return;
        }
        const rect = ev.currentTarget.getBoundingClientRect();
        const { W, L, R } = t.geom;
        const inSvg = ((ev.clientX - rect.left) / rect.width) * W;
        const n = t.labels.length;
        const span = (W - L - R) / Math.max(1, n - 1);
        const i = Math.max(0, Math.min(n - 1, Math.round((inSvg - L) / span)));
        t.hover = {
            i,
            x: t.labels[i].x,
            left: (t.labels[i].x / W) * 100,
            label: t.labels[i].text,
            rows: t.series.map((sr) => ({ name: sr.name, color: sr.color, v: sr.values[i] })),
        };
    }
    onTrendLeave() {
        this.state.trend.hover = null;
    }

    /** The card shows the top few; the chips above it list every campaign,
     *  so nothing is hidden - this only keeps the row a sane height. */
    get visibleCampaigns() {
        return this.state.campaigns.slice(0, 3);
    }
    get hiddenCampaignCount() {
        return Math.max(0, this.state.campaigns.length - 3);
    }

    /** The status holding the biggest share - the donut's centre label. */
    get topShare() {
        const best = [...this.state.share].sort((a, b) => b.count - a.count)[0];
        return best && best.count ? best : null;
    }

    async setCampaign(id) {
        this.state.campaignId = id;
        this.state.loading = true;
        await this.load();
    }

    /** The window immediately before the selected one, for the trend figure. */
    _previousWindow() {
        const days = this.periodDays;
        if (!days) {
            return null;
        }
        return [
            ["create_date", ">=", this._since(days * 2)],
            ["create_date", "<", this._since(days)],
        ];
    }
    async setPeriod(key) {
        this.state.period = key;
        this.state.loading = true;
        await this.load();
    }

    // ---- aggregation helpers -----------------------------------------
    async _sum(model, domain, field) {
        const groups = await this.orm.formattedReadGroup(model, domain, [], [`${field}:sum`]);
        return (groups[0] && groups[0][`${field}:sum`]) || 0;
    }
    async _avg(model, domain, field) {
        const groups = await this.orm.formattedReadGroup(model, domain, [], [`${field}:avg`]);
        return Math.round((groups[0] && groups[0][`${field}:avg`]) || 0);
    }
    /** Percentage change, or null when there is no base to compare against. */
    _trend(now, before) {
        if (!before) {
            return null;
        }
        return Math.round(((now - before) / before) * 100);
    }

    // ---- geometry ------------------------------------------------------
    /** Column heights as percentages of the tallest value. */
    _columns(items, key) {
        const max = Math.max(1, ...items.map((i) => i[key] || 0));
        return items.map((i, idx) => ({
            ...i,
            pct: Math.max(2, Math.round(((i[key] || 0) / max) * 100)),
            color: RAMP[Math.min(idx, RAMP.length - 1)],
        }));
    }
    /** Percentages for value and count at once, so the toggle is instant. */
    _columnsDual(items) {
        const byValue = this._columns(items, "value");
        const byCount = this._columns(items, "count");
        return byValue.map((i, idx) => ({ ...i, pctValue: i.pct, pctCount: byCount[idx].pct }));
    }
    /** What a column should show, given the chosen measure. */
    colPct(col) {
        return this.measureIsValue ? col.pctValue : col.pctCount;
    }
    colLabel(col) {
        return this.measureIsValue ? this.fmtCompact(col.value) : col.count;
    }
    _bars(items, key, color) {
        const max = Math.max(1, ...items.map((i) => i[key] || 0));
        return items.map((i) => ({
            ...i,
            pct: Math.max(2, Math.round(((i[key] || 0) / max) * 100)),
            color: color || C.open,
        }));
    }
    /** Donut segments: dash/offset pairs around a circle of r=42. */
    _donut(segments) {
        const total = segments.reduce((a, s) => a + (s.value || 0), 0) || 1;
        const C = 2 * Math.PI * 42;
        let acc = 0;
        return segments.map((s) => {
            const frac = (s.value || 0) / total;
            const seg = {
                ...s,
                pct: Math.round(frac * 100),
                dash: `${frac * C} ${C - frac * C}`,
                offset: -acc * C,
            };
            acc += frac;
            return seg;
        });
    }

    /** Semicircle gauge: 0-100 mapped onto a 180-degree arc of fixed length. */
    gaugeDash(value) {
        const circumference = Math.PI * 80; // r = 80, half circle
        const filled = (Math.min(100, Math.max(0, value)) / 100) * circumference;
        return `${filled} ${circumference - filled}`;
    }
    /** Meter severity: the fill carries the state, never hue alone (label beside it). */
    gaugeColor(value) {
        if (value >= 50) {
            return C.won;
        }
        return value >= 25 ? "#eda100" : C.lost;
    }

    async load() {
        const orm = this.orm;
        const camp = this._campaign();
        const win = [...this._window(), ...camp];          // created in period
        const closedWin = [...this._closedWindow(), ...camp]; // closed in period
        // Open is a snapshot: every live deal, regardless of age.
        const open = [OPP, ["ac_deal_status", "=", "open"], ...camp];
        const won = [OPP, ["ac_deal_status", "=", "won"], ...closedWin];
        const lost = [OPP, ["ac_deal_status", "=", "lost"], ...closedWin];
        // Journey events belong to a deal, so the pipeline filter reaches them
        // through the deal rather than directly.
        const eventDomain = this.state.campaignId
            ? [["lead_id.campaign_id", "=", this.state.campaignId]]
            : [];
        // Lost deals are archived, so they need active_test off to be counted.
        const ARCHIVED = { context: { active_test: false } };
        const prev = this._previousWindow();

        const [
            openCount, wonCount, lostCount, leadCount, overdueCount, urgentCount,
            contacts, externalContacts, tasksDone,
            rottingCount, atRiskCount, noNextStepCount, noCloseDateCount, unpricedCount,
            avgCycleDays, createdCount, leadsCreated, leadsConverted, lostReasonGroups,
            openValue, wonValue, lostValue,
            pLow, pMedium, pHigh, pVeryHigh,
            byStage, sentimentGroups, ownerGroups, velocityGroups, topDeals, activity,
            campaignRows, campaignGroups,
            createdMonths, wonMonths, lostMonths,
        ] = await Promise.all([
            orm.searchCount("crm.lead", open),
            orm.searchCount("crm.lead", won),
            orm.searchCount("crm.lead", lost, ARCHIVED),
            orm.searchCount("crm.lead", [["type", "=", "lead"], ...win]),
            orm.searchCount("crm.lead", [...open, ["activity_state", "=", "overdue"]]),
            orm.searchCount("crm.lead", [...open, ["priority", "=", "3"]]),
            orm.searchCount("res.partner", [["is_company", "=", false]]),
            orm.searchCount("res.partner", [["contact_scope", "=", "external"]]),
            orm.searchCount("crm.activity.log", win.length ? [["date_done", ">=", this._since(this.periodDays)]] : []),
            // Pipeline health. All three are native signals the module stopped
            // having to invent once scoring was dropped.
            orm.searchCount("crm.lead", [...open, ["is_rotting", "=", true]]),
            // At risk: overdue OR nothing scheduled OR stalled. One OR-domain
            // rather than three counts added up, so a deal that is overdue AND
            // stalled is counted once, not twice.
            orm.searchCount("crm.lead", [...open, ...AT_RISK]),
            orm.searchCount("crm.lead", [...open, ["activity_state", "=", false]]),
            orm.searchCount("crm.lead", [...open, ["date_deadline", "=", false]]),
            // Deals with nothing in Expected Revenue: every money figure on the
            // page is blind to these.
            orm.searchCount("crm.lead", [...open, ["expected_revenue", "=", 0]]),
            // Speed and throughput.
            this._avg("crm.lead", won, "day_close"),
            orm.searchCount("crm.lead", [OPP, ...win]),
            // Lead -> deal conversion over the window.
            orm.searchCount("crm.lead", [["type", "=", "lead"], ...win]),
            orm.searchCount("crm.lead", [OPP, ["date_conversion", "!=", false], ...win]),
            // Why deals are lost.
            orm.formattedReadGroup("crm.lead", lost, ["lost_reason_id"], ["__count"], {
                context: { active_test: false },
            }),
            this._sum("crm.lead", open, "expected_revenue"),
            this._sum("crm.lead", won, "expected_revenue"),
            this._sum("crm.lead", lost, "expected_revenue"),
            orm.searchCount("crm.lead", [...open, ["priority", "=", "0"]]),
            orm.searchCount("crm.lead", [...open, ["priority", "=", "1"]]),
            orm.searchCount("crm.lead", [...open, ["priority", "=", "2"]]),
            orm.searchCount("crm.lead", [...open, ["priority", "=", "3"]]),
            orm.formattedReadGroup("crm.lead", open, ["stage_id"], ["__count", "expected_revenue:sum"]),
            orm.formattedReadGroup("crm.activity.log", [], ["sentiment"], ["__count"]),
            orm.formattedReadGroup("crm.lead", open, ["user_id"], ["__count", "expected_revenue:sum"]),
            orm.formattedReadGroup(
                "crm.stage.history", [["date_left", "!=", false]], ["stage_id"], ["duration_hours:avg"]
            ),
            orm.searchRead(
                "crm.lead", open,
                ["name", "stage_id", "priority", "probability", "expected_revenue", "user_id", "partner_id"],
                { limit: 6, order: "expected_revenue desc" }
            ),
            orm.searchRead(
                "crm.journey.event", eventDomain,
                ["event", "lead_id", "value_to", "create_date", "user_id"],
                { limit: 7, order: "create_date desc" }
            ),
            // Campaigns, with their own figures - a campaign you just created
            // shows up here immediately, even before it holds a single deal.
            orm.searchRead("utm.campaign", [], ["name"], { order: "id" }),
            orm.formattedReadGroup(
                "crm.lead", [OPP, ["ac_deal_status", "=", "open"]], ["campaign_id"],
                ["__count", "expected_revenue:sum"]
            ),
            // Trend: six calendar months, deliberately independent of the period
            // chips, so the shape of the business is readable whatever window
            // is selected. Counts only - one unit, one axis.
            orm.formattedReadGroup(
                "crm.lead",
                [OPP, ["create_date", ">=", this._monthsAgoISO(TREND_MONTHS)], ...camp],
                ["create_date:month"], ["__count"]
            ),
            orm.formattedReadGroup(
                "crm.lead",
                [OPP, ["ac_deal_status", "=", "won"],
                 ["date_closed", ">=", this._monthsAgoISO(TREND_MONTHS)], ...camp],
                ["date_closed:month"], ["__count"]
            ),
            orm.formattedReadGroup(
                "crm.lead",
                [OPP, ["ac_deal_status", "=", "lost"],
                 ["date_closed", ">=", this._monthsAgoISO(TREND_MONTHS)], ...camp],
                ["date_closed:month"], ["__count"], ARCHIVED
            ),
        ]);

        // Trend: this window against the one before it.
        let wonTrend = null;
        let openTrend = null;
        if (prev) {
            // Same window length, immediately before, on the same date field.
            const prevClosed = [
                ["date_closed", ">=", this._since(this.periodDays * 2)],
                ["date_closed", "<", this._since(this.periodDays)],
                ...this._campaign(),
            ];
            const [prevWon, prevCreated] = await Promise.all([
                this._sum("crm.lead", [OPP, ["ac_deal_status", "=", "won"], ...prevClosed], "expected_revenue"),
                orm.searchCount("crm.lead", [OPP, ...prev, ...this._campaign()]),
            ]);
            wonTrend = this._trend(wonValue, prevWon);
            openTrend = this._trend(createdCount, prevCreated);
        }

        const closed = wonCount + lostCount;
        this.state.k = {
            openCount, wonCount, lostCount, leadCount, overdueCount, urgentCount,
            contacts, externalContacts, tasksDone,
            openValue, wonValue, lostValue,
            rottingCount, atRiskCount, noNextStepCount, noCloseDateCount, unpricedCount,
            avgCycleDays, createdCount,
            conversionRate: leadsCreated ? Math.round((leadsConverted / leadsCreated) * 100) : null,
            leadsCreated, leadsConverted,
            wonTrend, openTrend,
            avgDealValue: openCount ? openValue / openCount : 0,
            winRate: closed ? Math.round((wonCount / closed) * 100) : 0,
        };

        // Pipeline value by stage - the hero chart.
        this.state.stages = this._columnsDual(
            byStage.map((g) => ({
                name: (g.stage_id && g.stage_id[1]) || "Undefined",
                id: g.stage_id && g.stage_id[0],
                count: g.__count || 0,
                value: g["expected_revenue:sum"] || 0,
            }))
        );

        // Nothing priced yet but deals on the board -> show counts instead of a
        // page full of zeros. An explicit choice by the user wins.
        if (!this.state.measureLocked) {
            const anyValue = openValue || wonValue || lostValue;
            this.state.measure = anyValue ? "value" : (openCount || wonCount ? "count" : "value");
        }

        // Status share - a donut, each slice directly labelled beside it.
        const total = openCount + wonCount + lostCount || 1;
        this.state.share = [
            { label: "Open", count: openCount, value: openValue, color: C.open, status: "open" },
            { label: "Won", count: wonCount, value: wonValue, color: C.won, status: "won" },
            { label: "Lost", count: lostCount, value: lostValue, color: C.lost, status: "lost" },
        ].map((s) => ({ ...s, pct: Math.round((s.count / total) * 100) }));
        this.state.donut = this._donut(
            this.state.share.map((s) => ({ ...s, value: s.count })));

        // Priority of open deals - ordered, so one hue stepped light to dark.
        this.state.priority = this._columns(
            [
                { label: "Low", count: pLow, priority: "0" },
                { label: "Medium", count: pMedium, priority: "1" },
                { label: "High", count: pHigh, priority: "2" },
                { label: "Urgent", count: pVeryHigh, priority: "3" },
            ],
            "count"
        );

        const sent = { positive: 0, neutral: 0, negative: 0 };
        for (const g of sentimentGroups) {
            const key = g.sentiment || "neutral";
            sent[key] = (sent[key] || 0) + (g.__count || 0);
        }
        sent.total = sent.positive + sent.neutral + sent.negative;
        this.state.sentiment = sent;

        this.state.owners = this._bars(
            ownerGroups
                .map((g) => ({
                    name: (g.user_id && g.user_id[1]) || "Unassigned",
                    id: g.user_id && g.user_id[0],
                    count: g.__count || 0,
                    value: g["expected_revenue:sum"] || 0,
                }))
                .sort((a, b) => b.value - a.value)
                .slice(0, 5),
            "value"
        ).map((o, idx) => ({ ...o, color: catColor(idx) }));

        this.state.velocity = this._bars(
            velocityGroups
                .map((g) => ({
                    name: (g.stage_id && g.stage_id[1]) || "Undefined",
                    hours: Math.round((g["duration_hours:avg"] || 0) * 10) / 10,
                }))
                .sort((a, b) => b.hours - a.hours)
                .slice(0, 5),
            "hours"
        );

        // Why we lose - magnitude by reason, so one hue, labelled.
        this.state.lostReasons = this._bars(
            lostReasonGroups
                .map((g) => ({
                    name: (g.lost_reason_id && g.lost_reason_id[1]) || "No reason given",
                    id: g.lost_reason_id && g.lost_reason_id[0],
                    count: g.__count || 0,
                }))
                .sort((a, b) => b.count - a.count)
                .slice(0, 5),
            "count",
            C.lost
        );

        this.state.topDeals = topDeals.map((d) => ({
            id: d.id,
            name: d.name,
            stage: (d.stage_id && d.stage_id[1]) || "",
            owner: (d.user_id && d.user_id[1]) || "Unassigned",
            contact: (d.partner_id && d.partner_id[1]) || "No contact yet",
            stars: Number(d.priority || 0),
            probability: Math.round(d.probability || 0),
            value: d.expected_revenue || 0,
        }));

        this.state.activity = activity.map((e) => ({
            id: e.id,
            event: e.event,
            label: this.eventLabel(e.event),
            deal: (e.lead_id && e.lead_id[1]) || "-",
            leadId: e.lead_id && e.lead_id[0],
            to: e.value_to || "",
            who: (e.user_id && e.user_id[1]) || "",
            when: this.fmtWhen(e.create_date),
        }));

        // Pipelines: counts come from the unfiltered open set, so the card is a
        // stable overview rather than a reflection of the current filter.
        const perCampaign = {};
        let unassigned = { count: 0, value: 0 };
        for (const g of campaignGroups) {
            const row = { count: g.__count || 0, value: g["expected_revenue:sum"] || 0 };
            if (g.campaign_id) {
                perCampaign[g.campaign_id[0]] = row;
            } else {
                unassigned = row;   // deals that belong to no campaign yet
            }
        }
        const maxCount = Math.max(
            1,
            ...campaignRows.map((c) => (perCampaign[c.id] || {}).count || 0),
            unassigned.count
        );
        this.state.campaigns = campaignRows.map((c, idx) => ({
            id: c.id,
            name: c.name,
            count: (perCampaign[c.id] || {}).count || 0,
            value: (perCampaign[c.id] || {}).value || 0,
            pct: Math.round((((perCampaign[c.id] || {}).count || 0) / maxCount) * 100),
            color: catColor(idx),
        }));
        this.state.k.noCampaignCount = unassigned.count;
        this.state.k.noCampaignValue = unassigned.value;
        // A campaign deleted elsewhere should not stay selected.
        if (this.state.campaignId && !campaignRows.some((c) => c.id === this.state.campaignId)) {
            this.state.campaignId = null;
        }

        this.state.trend = this._lines(this._months(TREND_MONTHS), [
            { name: "Created", color: C.open,
              values: this._bucket(createdMonths, "create_date:month", this._months(TREND_MONTHS)) },
            { name: "Won", color: C.won,
              values: this._bucket(wonMonths, "date_closed:month", this._months(TREND_MONTHS)) },
            { name: "Lost", color: C.lost,
              values: this._bucket(lostMonths, "date_closed:month", this._months(TREND_MONTHS)) },
        ]);

        this.state.k.periodNote = this.periodDays
            ? `closed figures cover the last ${this.periodDays} days`
            : "all time";
        this.state.updatedAt = new Date().toLocaleTimeString();
        this.state.loading = false;
    }

    // ---- formatting ---------------------------------------------------
    eventLabel(event) {
        return {
            enters_pipeline: "Entered the pipeline",
            stage_changed: "Moved stage",
            status_changed: "Status changed",
            owner_changed: "Owner changed",
            value_changed: "Value changed",
            task_completed: "Task completed",
        }[event] || event;
    }
    eventIcon(event) {
        return {
            enters_pipeline: "fa-sign-in",
            stage_changed: "fa-arrow-right",
            status_changed: "fa-flag",
            owner_changed: "fa-user",
            value_changed: "fa-money",
            task_completed: "fa-check",
        }[event] || "fa-circle";
    }
    fmtMoney(v) {
        return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(v || 0);
    }
    fmtCompact(v) {
        return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(v || 0);
    }
    fmtWhen(value) {
        if (!value) {
            return "";
        }
        const then = new Date(value.replace(" ", "T") + "Z");
        const mins = Math.round((Date.now() - then.getTime()) / 60000);
        if (mins < 60) {
            return `${Math.max(1, mins)}m ago`;
        }
        if (mins < 1440) {
            return `${Math.round(mins / 60)}h ago`;
        }
        return then.toLocaleDateString(undefined, { day: "numeric", month: "short" });
    }

    // ---- refresh --------------------------------------------------------
    async refresh() {
        this.state.loading = true;
        await this.load();
    }
    _startAuto() {
        this._stopAuto();
        this._timer = browser.setInterval(() => this.load(), REFRESH_MS);
    }
    _stopAuto() {
        if (this._timer) {
            browser.clearInterval(this._timer);
            this._timer = null;
        }
    }
    toggleAuto() {
        this.state.autoRefresh = !this.state.autoRefresh;
        if (this.state.autoRefresh) {
            this._startAuto();
        } else {
            this._stopAuto();
        }
    }

    // ---- drill-through / actions ----------------------------------------
    /** Drill-through for a SNAPSHOT figure (open deals, risk). The period
     *  filters what closed or was created in it; it must not silently hide a
     *  live deal just because it was created before the window. */
    openSnapshot(domain, name, context = {}) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name,
            res_model: "crm.lead",
            domain: [...domain, ...this._campaign()],
            context,
            views: [[false, "list"], [false, "form"]],
        });
    }
    openDeals(domain, name, context = {}) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name,
            res_model: "crm.lead",
            domain: [...domain, ...this._window(), ...this._campaign()],
            context,
            views: [[false, "list"], [false, "form"]],
        });
    }
    openStatus(status) {
        const ctx = status === "lost" ? { active_test: false } : {};
        this.openDeals(
            [["type", "=", "opportunity"], ["ac_deal_status", "=", status]],
            `${status.charAt(0).toUpperCase()}${status.slice(1)} deals`,
            ctx
        );
    }
    openList(model, name, domain = []) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name,
            res_model: model,
            domain,
            views: [[false, "list"], [false, "form"]],
        });
    }
    openRecord(model, resId) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: model,
            res_id: resId,
            views: [[false, "form"]],
        });
    }
    newDeal() {
        this.newDealHere();
    }
    openPipelines() {
        this.action.doAction("sdlc_CRM.action_sdlc_pipelines");
    }
    /** The deals of one campaign, on the board. */
    openCampaignBoard(id, name) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: name,
            res_model: "crm.lead",
            domain: [["type", "=", "opportunity"], ["campaign_id", "=", id]],
            context: { default_campaign_id: id, default_type: "opportunity" },
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
        });
    }
    openBoard() {
        this.action.doAction("sdlc_CRM.action_sdlc_pipeline_board");
    }
    openCampaigns() {
        this.action.doAction("sdlc_CRM.action_sdlc_campaigns");
    }
    newCampaign() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "New campaign",
            res_model: "utm.campaign",
            views: [[false, "form"]],
        });
    }
    /** New deal, dropped straight into the pipeline currently being viewed. */
    newDealHere() {
        const ctx = { default_type: "opportunity" };
        if (this.state.campaignId) {
            ctx.default_campaign_id = this.state.campaignId;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "New deal",
            res_model: "crm.lead",
            views: [[false, "form"]],
            context: ctx,
        });
    }
}

registry.category("actions").add("sdlc_crm_dashboard", SdlcCrmDashboard);
