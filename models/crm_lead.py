# -*- coding: utf-8 -*-
import logging
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    # ------------------------------------------------------------------
    # Multi-contact deals (AC: primary contact + secondary contacts)
    # Odoo's crm.lead has exactly one partner_id; AC deals carry many.
    # ------------------------------------------------------------------
    contact_ids = fields.Many2many(
        'res.partner', 'crm_lead_partner_rel', 'lead_id', 'partner_id',
        string='Deal Contacts',
        help="All contacts on this deal. partner_id is the primary contact and "
             "is always kept inside this set (blueprint C4 #12).")
    contact_count = fields.Integer(compute='_compute_contact_count')

    account_id = fields.Many2one(
        'res.partner', string='Account',
        compute='_compute_account_id', store=True, readonly=False,
        domain="[('is_company', '=', True)]",
        help="Company the deal belongs to (AC 'Account'). Defaults to the primary "
             "contact's commercial (company) partner.")

    # ------------------------------------------------------------------
    # Per-deal currency (AC stores currency on the deal, not the company)
    # ------------------------------------------------------------------
    deal_currency_id = fields.Many2one(
        'res.currency', string='Deal Currency',
        default=lambda s: s.env.company.currency_id,
        help="AC stores currency per-deal, inherited from the pipeline default. "
             "expected_revenue is expressed in this currency.")
    expected_revenue_company = fields.Monetary(
        string='Expected Revenue (Company Currency)',
        currency_field='company_currency',
        compute='_compute_expected_revenue_company', store=True,
        help="expected_revenue converted to company currency so multi-currency "
             "pipelines produce meaningful totals (blueprint F2 #15).")

    # ------------------------------------------------------------------
    # AC-style decoupled status. Odoo couples won-ness to the stage
    # (crm.stage.is_won). We keep Odoo's native won_status as the source of
    # truth but expose an explicit Open/Won/Lost mirror for journey triggers,
    # matching AC's status int (0/1/2).
    # ------------------------------------------------------------------
    ac_deal_status = fields.Selection(
        [('open', 'Open'), ('won', 'Won'), ('lost', 'Lost')],
        string='Deal Status', compute='_compute_ac_deal_status',
        store=True, index=True, tracking=True)

    # ------------------------------------------------------------------
    # Scoring (populated by sdlc_crm scoring engine)
    # ------------------------------------------------------------------
    deal_score = fields.Integer(
        string='Deal Score', readonly=True, index=True,
        help="Decay-based priority score. High = hot, decaying = stalling "
             "(blueprint B3).")

    stage_history_ids = fields.One2many('crm.stage.history', 'lead_id', string='Stage History')
    journey_event_ids = fields.One2many('crm.journey.event', 'lead_id', string='Journey Events')
    journey_event_count = fields.Integer(compute='_compute_journey_event_count')

    # ==================================================================
    # Computes
    # ==================================================================
    @api.depends('contact_ids')
    def _compute_contact_count(self):
        for lead in self:
            lead.contact_count = len(lead.contact_ids)

    @api.depends('partner_id', 'partner_id.commercial_partner_id')
    def _compute_account_id(self):
        for lead in self:
            if lead.partner_id:
                commercial = lead.partner_id.commercial_partner_id
                # Only auto-set to a *company*; individuals with no company
                # leave account blank rather than pointing the account at a person.
                lead.account_id = commercial if commercial.is_company else lead.account_id
            # else: keep whatever was there (manual override supported).

    @api.depends('expected_revenue', 'deal_currency_id', 'date_deadline', 'company_id')
    def _compute_expected_revenue_company(self):
        for lead in self:
            company = lead.company_id or self.env.company
            company_currency = lead.company_currency or company.currency_id
            deal_currency = lead.deal_currency_id or company_currency
            if not lead.expected_revenue:
                lead.expected_revenue_company = 0.0
                continue
            lead.expected_revenue_company = deal_currency._convert(
                lead.expected_revenue, company_currency, company,
                lead.date_deadline or fields.Date.context_today(lead))

    @api.depends('won_status', 'active', 'stage_id.is_won')
    def _compute_ac_deal_status(self):
        for lead in self:
            if lead.won_status == 'won' or (lead.active and lead.stage_id.is_won):
                lead.ac_deal_status = 'won'
            elif not lead.active or lead.won_status == 'lost':
                lead.ac_deal_status = 'lost'
            else:
                lead.ac_deal_status = 'open'

    @api.depends('journey_event_ids')
    def _compute_journey_event_count(self):
        for lead in self:
            lead.journey_event_count = len(lead.journey_event_ids)

    # ==================================================================
    # Constraints (blueprint C4 #12: a deal always has a primary contact)
    # ==================================================================
    @api.constrains('type', 'partner_id', 'contact_ids')
    def _check_primary_contact(self):
        # Only enforce on opportunities (deals). Leads may be partner-less
        # while still being qualified -- that is Odoo's lead/opportunity gate.
        for lead in self:
            if lead.type == 'opportunity' and not lead.partner_id and not lead.contact_ids:
                raise ValidationError(_(
                    "A deal must have at least one contact "
                    "(ActiveCampaign rule: a deal cannot exist without a primary contact)."))

    # ==================================================================
    # Create / Write -- fire the flow events
    # ==================================================================
    @api.model_create_multi
    def create(self, vals_list):
        leads = super().create(vals_list)
        for lead in leads:
            lead._sync_primary_contact()
            if lead.type == 'opportunity':
                # AC: creating a deal fires "Enters a pipeline", NOT "stage
                # changes" for the initial placement (blueprint C4 #14).
                lead._fire_journey_event('enters_pipeline', to=lead.team_id.display_name)
                if lead.stage_id:
                    # sudo: stage history is system-generated bookkeeping; a
                    # non-manager moving/creating a deal must not need direct
                    # create rights on crm.stage.history.
                    self.env['crm.stage.history'].sudo()._log_transition(lead, previous_stage=False)
        return leads

    def write(self, vals):
        # Snapshot the fields we emit events for, before the write.
        tracked = {
            l.id: {
                'stage': l.stage_id,
                'user': l.user_id,
                'revenue': l.expected_revenue,
                'status': l.ac_deal_status,
            } for l in self
        }
        res = super().write(vals)
        for lead in self:
            if lead.type != 'opportunity':
                continue
            before = tracked.get(lead.id, {})
            if 'stage_id' in vals and lead.stage_id != before.get('stage'):
                previous = before.get('stage')
                lead._fire_journey_event(
                    'stage_changed',
                    frm=previous.display_name if previous else False,
                    to=lead.stage_id.display_name)
                self.env['crm.stage.history'].sudo()._log_transition(lead, previous_stage=previous)
            if 'user_id' in vals and lead.user_id != before.get('user'):
                prev_user = before.get('user')
                lead._fire_journey_event(
                    'owner_changed',
                    frm=prev_user.display_name if prev_user else False,
                    to=lead.user_id.display_name)
            if 'expected_revenue' in vals and lead.expected_revenue != before.get('revenue'):
                lead._fire_journey_event(
                    'value_changed',
                    frm=before.get('revenue'), to=lead.expected_revenue)
            if lead.ac_deal_status != before.get('status'):
                lead._fire_journey_event(
                    'status_changed',
                    frm=before.get('status'), to=lead.ac_deal_status)
        return res

    # ==================================================================
    # Helpers
    # ==================================================================
    def _sync_primary_contact(self):
        """Enforce AC's invariant: the primary contact is always a member of
        the deal's contact set."""
        for lead in self:
            if lead.partner_id and lead.partner_id not in lead.contact_ids:
                lead.contact_ids = [(4, lead.partner_id.id)]

    def _fire_journey_event(self, event, frm=False, to=False, note=False):
        """Record a flow event. Central, consistent event stream that
        base.automation rules and reports key off (see data/base_automation_data.xml)."""
        self.ensure_one()
        return self.env['crm.journey.event'].sudo().create({
            'event': event,
            'res_model': 'crm.lead',
            'res_id': self.id,
            'lead_id': self.id,
            'partner_id': self.partner_id.id or False,
            'value_from': self._event_str(frm),
            'value_to': self._event_str(to),
            'note': note,
        })

    @staticmethod
    def _event_str(val):
        if val in (False, None):
            return False
        return str(val)

    def action_view_stage_history(self):
        """Smart-button action: open this deal's journey-event trail."""
        self.ensure_one()
        return {
            'name': _("Journey Events"),
            'type': 'ir.actions.act_window',
            'res_model': 'crm.journey.event',
            'view_mode': 'list,form',
            'domain': [('lead_id', '=', self.id)],
            'context': {'default_lead_id': self.id, 'default_res_model': 'crm.lead', 'default_res_id': self.id},
        }

    # ------------------------------------------------------------------
    # Self-propelling stage->task loop (blueprint B5).
    # Called by the "Deal stage changes -> create stage task" automation.
    # ------------------------------------------------------------------
    def _create_stage_activity(self):
        """Create the stage-appropriate task for the deal owner."""
        Activity = self.env['mail.activity']
        for lead in self:
            stage = lead.stage_id
            act_type = stage.default_activity_type_id
            if not act_type:
                continue
            due = fields.Date.context_today(lead) + relativedelta(
                days=stage.default_activity_delay or 0)
            # NB: no _() here -- this runs inside a base.automation server-action
            # frame where Odoo's translation uid-inspection raises (a local
            # named 'user' shadows the frame lookup).
            summary = stage.default_activity_summary or ("Work stage: %s" % stage.name)
            lead.activity_schedule(
                activity_type_id=act_type.id,
                summary=summary,
                date_deadline=due,
                user_id=lead.user_id.id or self.env.uid,
            )
        return True

    # ------------------------------------------------------------------
    # Score->deal handoff target (blueprint B3 / E3).
    # Called from the res.partner "score crosses threshold" automation.
    # ------------------------------------------------------------------
    @api.model
    def _create_deal_from_contact(self, partner, team=None, stage=None, user=None):
        """Create an opportunity from a marketing contact that has crossed the
        contact-score threshold. Titling follows AC's 'Company - Product' habit."""
        team = team or self.env['crm.team'].search([], limit=1)
        # No _() here: invoked from a base.automation server-action frame.
        company_name = partner.commercial_partner_id.name or partner.name
        title = "%s - Opportunity" % company_name
        vals = {
            'name': title,
            'type': 'opportunity',
            'partner_id': partner.id,
            'contact_ids': [(4, partner.id)],
            'team_id': team.id if team else False,
            'user_id': (user or team.user_id).id if (user or (team and team.user_id)) else False,
            'expected_revenue': 0.0,
        }
        if stage:
            vals['stage_id'] = stage.id
        lead = self.create(vals)
        lead._fire_journey_event('deal_created', note="Created from contact score handoff")
        return lead
