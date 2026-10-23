# -*- coding: utf-8 -*-
import logging
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError

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
    lc_status_change_date = fields.Date(
        string='LC - Status Change', readonly=True, copy=False, tracking=True,
        help="Date the deal status (Open/Won/Lost) last changed. Every status "
             "change overwrites it; blank until the first change.")

    # ------------------------------------------------------------------
    # Loss analysis. Captured on the Mark Lost popup (crm.lead.lost), except
    # the stage, which is snapshotted automatically: a lost deal keeps its
    # stage_id, but nothing stops someone moving the card afterwards.
    # ------------------------------------------------------------------
    loss_category = fields.Selection(
        related='lost_reason_id.loss_category', store=True, string='Loss Category')
    lost_competitor = fields.Char('Lost To (Competitor)', tracking=True)
    lost_reengage_date = fields.Date(
        'Re-engage On', tracking=True,
        help="When to approach this account again.")
    lost_stage_id = fields.Many2one(
        'crm.stage', string='Lost at Stage', readonly=True, copy=False,
        help="Stage the deal was in when it was marked Lost.")

    # Odoo labels this "Sales Team"; here it is the pipeline the deal sits in.
    team_id = fields.Many2one(string='Pipeline')

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
    # Create / Write -- fire the flow events
    # ==================================================================
    # NB: a deal deliberately has no "must have a contact" constraint. A rep can
    # open one with just a company name and an email; the Leads layer is where
    # anonymous prospects live, and conversion fills the contact in.
    @api.model_create_multi
    def create(self, vals_list):
        leads = super().create(vals_list)
        for lead in leads:
            lead._sync_primary_contact()
            if lead.type == 'opportunity':
                lead._sync_prospect_contact()
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
                'type': l.type,
                'stage': l.stage_id,
                'user': l.user_id,
                'revenue': l.expected_revenue,
                'status': l.ac_deal_status,
            } for l in self
        }
        res = super().write(vals)
        status_changed = self.browse()
        if {'contact_name', 'email_from', 'phone', 'partner_name', 'type'} & set(vals):
            # details filled in later, or a lead just became a deal
            self._sync_prospect_contact()
        if 'partner_id' in vals:
            # AC's invariant has to hold on write too, not just create: a lead
            # converted into a deal gets its partner_id set here, so without
            # this the new primary contact would be missing from contact_ids
            # (and the Deal Contacts tab would come up empty).
            self._sync_primary_contact()
        for lead in self:
            if lead.type != 'opportunity':
                continue
            before = tracked.get(lead.id, {})
            # A lead converted into a deal *enters the pipeline*; it does not
            # "change stage" (same rule as create, blueprint C4 #14). Without
            # this, a converted lead would carry no stage history at all.
            converted = before.get('type') == 'lead'
            if converted:
                lead._fire_journey_event('enters_pipeline', to=lead.team_id.display_name)
                if lead.stage_id:
                    self.env['crm.stage.history'].sudo()._log_transition(lead, previous_stage=False)
            elif 'stage_id' in vals and lead.stage_id != before.get('stage'):
                previous = before.get('stage')
                lead._fire_journey_event(
                    'stage_changed',
                    frm=previous.display_name if previous else False,
                    to=lead.stage_id.display_name)
                # Clearing the stage must not open a history row: stage_id is
                # required there, so _log_transition would raise.
                if lead.stage_id:
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
                status_changed |= lead
                lead._fire_journey_event(
                    'status_changed',
                    frm=before.get('status'), to=lead.ac_deal_status)
        if status_changed:
            # Steve's 'LC - Status Change': any Open/Won/Lost transition
            # overwrites it. The nested write leaves the status untouched, so
            # it does not land back here. A loss also snapshots the stage.
            today = fields.Date.context_today(self)
            for lead in status_changed:
                vals = {'lc_status_change_date': today}
                if lead.ac_deal_status == 'lost':
                    vals['lost_stage_id'] = lead.stage_id.id
                lead.write(vals)
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

    @api.model
    def _sync_prospect_contact(self):
        """Store a deal's person in Contacts, flagged external.

        Every deal should leave a real contact behind - that is where call
        history, emails and future deals hang off. Leads are deliberately
        exempt: the lead layer exists precisely so an unqualified prospect can
        sit in the pipeline without a contact record, and converting the lead is
        what promotes it.

        The contact is created through Odoo's own ``_create_customer()``, so it
        is built from the deal's details exactly as a native conversion would,
        and it lands as ``contact_scope = 'external'`` - no login, no access.
        """
        for lead in self:
            if lead.type != 'opportunity' or lead.partner_id:
                continue
            if not (lead.contact_name or lead.email_from or lead.partner_name):
                continue  # nothing to build a contact from yet
            # Reuse an existing contact before minting one: _create_customer()
            # has no duplicate check, so without this every deal from the same
            # person creates another "John Smith".
            partner = lead._find_matching_partner()
            if not partner:
                partner = lead._create_customer()
            if partner:
                lead.partner_id = partner.id
                lead._sync_primary_contact()
        return True

    def action_create_contact(self):
        # Kept for the rare deal saved with no name, email or company at all:
        # _sync_prospect_contact() has nothing to work from in that case, so
        # this stays reachable. Normally the contact already exists by now.
        """Turn this prospect's inline details into a real contact.

        Prospects live on the deal itself (contact_name / partner_name /
        email_from / phone), so working a pipeline creates no ``res.partner``
        rows at all. This button is the only way one appears, and it is manual:
        a prospect becomes a contact when *you* say it is worth keeping.

        Reuses Odoo's own ``_create_customer()`` so the partner is built from
        lead data exactly as the native conversion would.
        """
        self.ensure_one()
        if self.partner_id:
            raise UserError(_("This deal already has a contact: %s", self.partner_id.display_name))
        if not (self.contact_name or self.partner_name or self.email_from):
            raise UserError(_(
                "Add a prospect name, company name or email first - there is "
                "nothing to build a contact from."))
        partner = self._create_customer()
        self.partner_id = partner.id
        self._sync_primary_contact()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Contact created"),
                'message': _("%s is now a contact linked to this deal.", partner.display_name),
                'type': 'success',
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

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

