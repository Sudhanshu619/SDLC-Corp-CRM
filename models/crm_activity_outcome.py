# -*- coding: utf-8 -*-
from odoo import fields, models


class CrmActivityOutcome(models.Model):
    """AC Task Outcome. Chosen when a task is completed; carries a sentiment
    that powers the Task Overview report's positive/negative split.

    One outcome can be reused across several task types (M2M), exactly as in
    ActiveCampaign (blueprint A4.3).
    """
    _name = 'crm.activity.outcome'
    _description = 'Task Outcome'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    sentiment = fields.Selection([
        ('positive', 'Positive'),
        ('neutral', 'Neutral'),
        ('negative', 'Negative'),
    ], default='neutral', required=True,
        help="Reporting dimension only (not workflow control): drives the "
             "Task Overview outcome-by-rep positive/negative split.")
    activity_type_ids = fields.Many2many(
        'mail.activity.type', 'crm_outcome_activity_type_rel',
        'outcome_id', 'activity_type_id', string='Task Types',
        help="Task types this outcome is offered for. Empty = offered for all.")
    active = fields.Boolean(default=True)

    # Optional: advance the deal to this stage when the outcome is chosen
    # (branch-on-outcome, blueprint B5). Left to automations by default.
    color = fields.Integer(string='Color Index')
