# -*- coding: utf-8 -*-
from odoo import api, fields, models


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
    # A stage belongs to the pipeline(s) named here; empty = every pipeline.
    team_ids = fields.Many2many(string='Pipelines')

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

    is_demo = fields.Boolean(
        string='Demo Stage',
        help="A deal entering this stage gets today's date as its Demo Date.")

    @api.model
    def default_get(self, fields_list):
        """A stage created from inside a pipeline belongs to that pipeline.

        Opening a pipeline (a crm.team) puts ``default_team_id`` in the context,
        so adding a column on that board lands here. Odoo leaves ``team_ids``
        empty, which makes the new stage *shared by every pipeline* -- the
        opposite of what someone building one pipeline's own cycle expects.
        Scoping it to the originating team keeps each pipeline's stages its own;
        clearing ``Sales Teams`` on the stage shares it again.
        """
        defaults = super().default_get(fields_list)
        team_id = self.env.context.get('default_team_id')
        if team_id and 'team_ids' in fields_list and not defaults.get('team_ids'):
            defaults['team_ids'] = [(6, 0, [team_id])]
        return defaults
