# -*- coding: utf-8 -*-
from odoo import api, fields, models


class CrmStageHistory(models.Model):
    """One row per stage transition of a deal.

    Blueprint F2 #10 / E7: AC has ``edate`` (entered-current-stage) but no
    stage history at all. Odoo natively keeps only ``date_last_stage_update``
    (the *current* entry time). Materialising a history row on every move is
    half a day of work that unlocks time-in-stage, per-stage conversion and
    cohort/velocity reporting -- the questions every sales manager asks and
    which are impossible to backfill later.
    """
    _name = 'crm.stage.history'
    _description = 'CRM Deal Stage History'
    _order = 'lead_id, date_entered'

    lead_id = fields.Many2one('crm.lead', required=True, ondelete='cascade', index=True)
    stage_id = fields.Many2one('crm.stage', required=True, ondelete='cascade')
    previous_stage_id = fields.Many2one('crm.stage', string='Previous Stage')
    user_id = fields.Many2one('res.users', string='Owner at entry')
    team_id = fields.Many2one('crm.team', string='Pipeline')

    date_entered = fields.Datetime(default=lambda s: fields.Datetime.now(), index=True)
    date_left = fields.Datetime('Left On')
    # Time spent in the *previous* stage, filled when the deal leaves it.
    duration_hours = fields.Float('Hours in previous stage', readonly=True)

    company_currency = fields.Many2one(
        'res.currency', related='lead_id.company_currency', readonly=True)
    expected_revenue = fields.Monetary(
        related='lead_id.expected_revenue', currency_field='company_currency', readonly=True)

    @api.model
    def _log_transition(self, lead, previous_stage):
        """Close the open history row for ``previous_stage`` and open a new one."""
        now = fields.Datetime.now()
        if previous_stage:
            open_row = self.search([
                ('lead_id', '=', lead.id),
                ('stage_id', '=', previous_stage.id),
                ('date_left', '=', False),
            ], limit=1, order='date_entered desc')
            if open_row:
                delta = now - open_row.date_entered
                open_row.write({
                    'date_left': now,
                    'duration_hours': delta.total_seconds() / 3600.0,
                })
        return self.create({
            'lead_id': lead.id,
            'stage_id': lead.stage_id.id,
            'previous_stage_id': previous_stage.id if previous_stage else False,
            'user_id': lead.user_id.id,
            'team_id': lead.team_id.id,
            'date_entered': now,
        })
