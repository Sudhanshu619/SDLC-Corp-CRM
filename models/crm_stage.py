# -*- coding: utf-8 -*-
from odoo import fields, models


class CrmStage(models.Model):
    """AC stage extras. Odoo's ``crm.stage`` is already richer than AC's
    (it has ``is_won``, ``requirements``, ``fold``, ``sequence`` and
    ``team_id`` scoping). We only add AC's per-stage *default task type*,
    which drives the self-propelling stage->task loop (blueprint B5).
    """
    _inherit = 'crm.stage'

    # When a deal enters this stage, the stage->task automation creates a task
    # of this type for the deal owner. Combined with the task type's native
    # ``triggered_next_type_id`` chaining, this builds AC's four-beat loop.
    default_activity_type_id = fields.Many2one(
        'mail.activity.type', string='Stage Task Type',
        help="Task automatically created for the deal owner when a deal enters "
             "this stage (ActiveCampaign's self-propelling stage->task loop).")
    default_activity_summary = fields.Char(
        string='Stage Task Summary',
        help="Summary used for the auto-created stage task.")
    default_activity_delay = fields.Integer(
        string='Task Due In (days)', default=2,
        help="Due date offset for the auto-created stage task.")
