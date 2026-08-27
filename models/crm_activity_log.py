# -*- coding: utf-8 -*-
from odoo import fields, models


class CrmActivityLog(models.Model):
    """Permanent record of a completed task.

    Blueprint E4 / F2 #1 -- THE biggest structural mismatch with Odoo:
    ``mail.activity._action_done()`` posts a message and then *unlinks the
    activity record*. Outcome, sentiment, assignee-at-completion, type and the
    created->completed duration are all destroyed, so AC's Task Overview report
    (volume + outcome + sentiment by rep) becomes uncomputable. We intercept
    completion and persist an immutable log row here.
    """
    _name = 'crm.activity.log'
    _description = 'Completed Task Log'
    _order = 'date_done desc, id desc'

    activity_type_id = fields.Many2one('mail.activity.type', string='Task Type', index=True)
    outcome_id = fields.Many2one('crm.activity.outcome', string='Outcome', index=True)
    sentiment = fields.Selection([
        ('positive', 'Positive'),
        ('neutral', 'Neutral'),
        ('negative', 'Negative'),
    ], index=True)
    user_id = fields.Many2one('res.users', string='Completed By', index=True)

    # Polymorphic parent (deal or contact), mirroring AC's reltype/relid.
    res_model = fields.Char('Related Model', index=True)
    res_id = fields.Many2oneReference('Related Record', model_field='res_model', index=True)
    lead_id = fields.Many2one('crm.lead', string='Deal', ondelete='set null', index=True)
    partner_id = fields.Many2one('res.partner', string='Contact', ondelete='set null', index=True)
    team_id = fields.Many2one('crm.team', string='Pipeline')

    summary = fields.Char('Summary')
    feedback = fields.Text('Completion Notes')

    date_deadline = fields.Date('Was Due')
    date_created = fields.Datetime('Task Created')
    date_done = fields.Datetime('Completed On', index=True)
    # Time-to-complete in hours, for cycle-time reporting.
    duration_hours = fields.Float('Hours to Complete', readonly=True)
    overdue = fields.Boolean('Was Overdue', readonly=True)
