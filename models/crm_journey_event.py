# -*- coding: utf-8 -*-
from odoo import api, fields, models


class CrmJourneyEvent(models.Model):
    """Immutable audit trail of every AC-style flow event that fires.

    ActiveCampaign's whole engine is driven by observable events on the deal,
    the contact and the account ("Deal stage changes", "Enters a pipeline",
    "Score changes", ...). We do not re-implement the visual journey canvas
    (that is a later phase); instead every meaningful transition is recorded
    here so the flow is *observable and testable*, and so ``base.automation``
    rules and reports can key off a single, consistent event stream.
    """
    _name = 'crm.journey.event'
    _description = 'CRM Journey Event (flow audit trail)'
    _order = 'create_date desc, id desc'
    _rec_name = 'event'

    event = fields.Selection([
        ('enters_pipeline', 'Enters a pipeline'),
        ('stage_changed', 'Deal stage changes'),
        ('status_changed', 'Deal status changes'),
        ('owner_changed', 'Deal owner changes'),
        ('value_changed', 'Deal value changes'),
        ('field_changed', 'Deal field changes'),
        ('task_completed', 'Task is completed'),
        ('score_changed', 'Score changes'),
        ('deal_created', 'Deal created (handoff)'),
        ('event_recorded', 'Behavioural event recorded'),
        ('account_field_changed', 'Account field changes'),
    ], required=True, index=True)

    # Polymorphic subject (deal / contact / account), mirroring AC's reltype/relid.
    res_model = fields.Char('Model', required=True, index=True)
    res_id = fields.Many2oneReference('Record', model_field='res_model', required=True, index=True)
    res_ref = fields.Reference(
        string='Record Ref',
        selection=[('crm.lead', 'Deal'), ('res.partner', 'Contact/Account')],
        compute='_compute_res_ref')

    lead_id = fields.Many2one('crm.lead', string='Deal', ondelete='cascade', index=True)
    partner_id = fields.Many2one('res.partner', string='Contact/Account', ondelete='cascade', index=True)
    user_id = fields.Many2one('res.users', string='By User', default=lambda s: s.env.user, index=True)

    value_from = fields.Char('From')
    value_to = fields.Char('To')
    note = fields.Char('Detail')

    @api.depends('res_model', 'res_id')
    def _compute_res_ref(self):
        for ev in self:
            if ev.res_model and ev.res_id:
                ev.res_ref = '%s,%s' % (ev.res_model, ev.res_id)
            else:
                ev.res_ref = False
