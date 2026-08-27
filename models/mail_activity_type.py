# -*- coding: utf-8 -*-
from odoo import fields, models


class MailActivityType(models.Model):
    """AC Task Type. Odoo's mail.activity.type already covers this and adds
    native chaining (``chaining_type='trigger'`` + ``triggered_next_type_id``),
    which reproduces AC's ``done_automation`` pointer. We only link the allowed
    outcomes so the completion UI offers the right ones (blueprint F2 #16).
    """
    _inherit = 'mail.activity.type'

    outcome_ids = fields.Many2many(
        'crm.activity.outcome', 'crm_outcome_activity_type_rel',
        'activity_type_id', 'outcome_id', string='Allowed Outcomes')
