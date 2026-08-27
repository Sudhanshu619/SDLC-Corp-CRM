/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { browser } from "@web/core/browser/browser";

const OPP = ["type", "=", "opportunity"];

// Palette used by the SVG/CSS charts. Kept here (not hard-coded in the
// template) so the donut legend and the bars stay in sync.
const STATUS_COLORS = { open: "#3b82f6", won: "#22c55e", lost: "#ef4444" };
const BAR_PALETTE = ["#6366f1", "#8b5cf6", "#0ea5e9", "#14b8a6", "#f59e0b", "#ec4899"];

// How often the dashboard silently re-queries the server (ms). Auto-refresh
// is on by default so edits made elsewhere show up without a manual reload.
const REFRESH_MS = 30000;

export class SdlcCrmDashboard extends Component {
    static template = "sdlc_CRM.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            loading: true,
            autoRefresh: true,
            updatedAt: "",
            k: {},
            stages: [],
            sentiment: {},
            scoreBuckets: [],
            owners: [],
            velocity: [],
            topDeals: [],
            donut: [],
        });
        this._timer = null;
        onWillStart(async () => {
            await this.load();
            this._startAuto();
        });
        onWillUnmount(() => this._stopAuto());
    }

    // ---- readGroup helpers -------------------------------------------
    async _sum(model, domain, field) {
        const groups = await this.orm.formattedReadGroup(model, domain, [], [`${field}:sum`]);
        return (groups[0] && groups[0][`${field}:sum`]) || 0;
    }
    async _avg(model, domain, field) {
        const groups = await this.orm.formattedReadGroup(model, domain, [], [`${field}:avg`]);
        return Math.round((groups[0] && groups[0][`${field}:avg`]) || 0);
    }

    // ---- geometry helpers for the charts -----------------------------
    _bars(items, key, palette) {
        const max = Math.max(1, ...items.map((i) => i[key] || 0));
        return items.map((i, idx) => ({
            ...i,
            pct: Math.round(((i[key] || 0) / max) * 100),
            color: (palette && palette[idx % palette.length]) || BAR_PALETTE[idx % BAR_PALETTE.length],
        }));
    }
    _donut(segments) {
        const total = segments.reduce((a, s) => a + (s.value || 0), 0) || 1;
        const R = 54;
        const C = 2 * Math.PI * R;
        let acc = 0;
        return segments.map((s) => {
            const frac = (s.value || 0) / total;
            const seg = {
                ...s,
                pct: Math.round(frac * 100),
                dash: frac * C,
                gap: C - frac * C,
                offset: -acc * C,
            };
            acc += frac;
            return seg;
        });
    }

    async load() {
        const orm = this.orm;
        const open = [OPP, ["ac_deal_status", "=", "open"]];
        const won = [OPP, ["ac_deal_status", "=", "won"]];
        const lost = [OPP, ["ac_deal_status", "=", "lost"]];
        // Marking a deal "Lost" archives the lead (active=False), and
        // search_count/read_group drop archived rows by default. Count lost
        // deals with active_test disabled so they actually show up.
        const INC_ARCHIVED = { context: { active_test: false } };

        const [
            openCount, wonCount, lostCount, hotCount, staleCount,
            contacts, handoff, tasksDone, events, dealsCreated,
            openValue, wonValue, avgDealScore, avgContactScore,
            bStale, bWarm, bEngaged, bHot,
            byStage, sentimentGroups, ownerGroups, velocityGroups, topDeals,
        ] = await Promise.all([
            orm.searchCount("crm.lead", open),
            orm.searchCount("crm.lead", won),
            orm.searchCount("crm.lead", lost, INC_ARCHIVED),
            orm.searchCount("crm.lead", [...open, ["deal_score", ">=", 90]]),
            orm.searchCount("crm.lead", [...open, ["deal_score", "<", 60]]),
            orm.searchCount("res.partner", [["is_company", "=", false]]),
            orm.searchCount("res.partner", [["crm_handoff_done", "=", true]]),
            orm.searchCount("crm.activity.log", []),
            orm.searchCount("crm.journey.event", []),
            orm.searchCount("crm.journey.event", [["event", "=", "deal_created"]]),
            this._sum("crm.lead", open, "expected_revenue"),
            this._sum("crm.lead", won, "expected_revenue"),
            this._avg("crm.lead", open, "deal_score"),
            this._avg("res.partner", [["is_company", "=", false]], "contact_score"),
            // score distribution buckets (open deals)
            orm.searchCount("crm.lead", [...open, ["deal_score", "<", 60]]),
            orm.searchCount("crm.lead", [...open, ["deal_score", ">=", 60], ["deal_score", "<", 75]]),
            orm.searchCount("crm.lead", [...open, ["deal_score", ">=", 75], ["deal_score", "<", 90]]),
            orm.searchCount("crm.lead", [...open, ["deal_score", ">=", 90]]),
            // grouped datasets
            orm.formattedReadGroup("crm.lead", open, ["stage_id"], ["__count", "expected_revenue:sum"]),
            orm.formattedReadGroup("crm.activity.log", [], ["sentiment"], ["__count"]),
            orm.formattedReadGroup("crm.lead", open, ["user_id"], ["__count", "expected_revenue:sum"]),
            orm.formattedReadGroup(
                "crm.stage.history", [["date_left", "!=", false]], ["stage_id"], ["duration_hours:avg"]
            ),
            orm.searchRead(
                "crm.lead", open,
                ["name", "stage_id", "deal_score", "expected_revenue", "user_id"],
                { limit: 8, order: "deal_score desc" }
            ),
        ]);

        const closed = wonCount + lostCount;
        this.state.k = {
            openCount, wonCount, lostCount, hotCount, staleCount,
            contacts, handoff, tasksDone, events, dealsCreated,
            openValue, wonValue, avgDealScore, avgContactScore,
            avgDealValue: openCount ? openValue / openCount : 0,
            winRate: closed ? Math.round((wonCount / closed) * 100) : 0,
        };

        // Pipeline funnel by stage
        this.state.stages = this._bars(
            byStage.map((g) => ({
                name: (g.stage_id && g.stage_id[1]) || "Undefined",
                count: g.__count || 0,
                value: g["expected_revenue:sum"] || 0,
            })),
            "value"
        );

        // Deal-status donut
        this.state.donut = this._donut([
            { label: "Open", value: openCount, color: STATUS_COLORS.open },
            { label: "Won", value: wonCount, color: STATUS_COLORS.won },
            { label: "Lost", value: lostCount, color: STATUS_COLORS.lost },
        ]);

        // Task sentiment
        const sent = { positive: 0, neutral: 0, negative: 0 };
        for (const g of sentimentGroups) {
            const key = g.sentiment || "neutral";
            sent[key] = (sent[key] || 0) + (g.__count || 0);
        }
        this.state.sentiment = sent;

        // Score distribution buckets
        this.state.scoreBuckets = this._bars(
            [
                { label: "Stalling (<60)", count: bStale, color: "#ef4444" },
                { label: "Warm (60–74)", count: bWarm, color: "#f59e0b" },
                { label: "Engaged (75–89)", count: bEngaged, color: "#0ea5e9" },
                { label: "Hot (90+)", count: bHot, color: "#22c55e" },
            ],
            "count"
        ).map((b, i) => ({ ...b, color: [ "#ef4444", "#f59e0b", "#0ea5e9", "#22c55e" ][i] }));

        // Owner leaderboard (top 6 by open pipeline value)
        const owners = ownerGroups
            .map((g) => ({
                name: (g.user_id && g.user_id[1]) || "Unassigned",
                count: g.__count || 0,
                value: g["expected_revenue:sum"] || 0,
            }))
            .sort((a, b) => b.value - a.value)
            .slice(0, 6);
        this.state.owners = this._bars(owners, "value");

        // Stage velocity (avg hours in stage)
        this.state.velocity = this._bars(
            velocityGroups.map((g) => ({
                name: (g.stage_id && g.stage_id[1]) || "Undefined",
                hours: Math.round((g["duration_hours:avg"] || 0) * 10) / 10,
            })),
            "hours"
        );

        // Top priority deals (live records)
        this.state.topDeals = topDeals.map((d) => ({
            id: d.id,
            name: d.name,
            stage: (d.stage_id && d.stage_id[1]) || "",
            owner: (d.user_id && d.user_id[1]) || "Unassigned",
            score: d.deal_score || 0,
            value: d.expected_revenue || 0,
        }));

        this.state.updatedAt = new Date().toLocaleTimeString();
        this.state.loading = false;
    }

    // ---- formatting ---------------------------------------------------
    fmtMoney(v) {
        return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(v || 0);
    }
    fmtCompact(v) {
        return new Intl.NumberFormat(undefined, {
            notation: "compact",
            maximumFractionDigits: 1,
        }).format(v || 0);
    }

    // ---- refresh / auto-refresh --------------------------------------
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

    // ---- drill-through -----------------------------------------------
    openDeals(domain, name) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name,
            res_model: "crm.lead",
            domain,
            views: [[false, "list"], [false, "form"]],
        });
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
}

registry.category("actions").add("sdlc_crm_dashboard", SdlcCrmDashboard);
