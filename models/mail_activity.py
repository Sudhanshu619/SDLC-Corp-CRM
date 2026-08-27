# -*- coding: utf-8 -*-
from odoo import api, fields, models


class MailActivity(models.Model):
    _inherit = 'mail.activity'

    outcome_id = fields.Many2one(
        'crm.activity.outcome', string='Outcome',
        domain="['|', ('activity_type_ids', '=', False), "
               "('activity_type_ids', 'in', activity_type_id)]",
        help="Chosen at completion. Only outcomes allowed for this task type "
             "are offered (blueprint F2 #16).")

    def _action_done(self, feedback=False, attachment_ids=None):
        """Persist a completion-log row BEFORE the activity is unlinked, then
        fire AC's 'Task is completed' event on the parent deal (blueprint E4)."""
        Log = self.env['crm.activity.log'].sudo()
        now = fields.Datetime.now()
        today = fields.Date.context_today(self)
        logs = []
        for act in self:
            duration = 0.0
            if act.create_date:
                duration = max(0.0, (now - act.create_date).total_seconds() / 3600.0)
            lead = act.res_id if act.res_model == 'crm.lead' else False
            partner = act.res_id if act.res_model == 'res.partner' else False
            team = False
            if act.res_model == 'crm.lead' and act.res_id:
                team = self.env['crm.lead'].browse(act.res_id).team_id.id
            logs.append({
                'activity_type_id': act.activity_type_id.id,
                'outcome_id': act.outcome_id.id,
                'sentiment': act.outcome_id.sentiment or 'neutral',
                'user_id': act.user_id.id,
                'res_model': act.res_model,
                'res_id': act.res_id,
                'lead_id': lead,
                'partner_id': partner,
                'team_id': team,
                'summary': act.summary,
                'feedback': feedback or (act.note and str(act.note)) or False,
                'date_deadline': act.date_deadline,
                'date_created': act.create_date,
                'date_done': now,
                'duration_hours': duration,
                'overdue': bool(act.date_deadline and act.date_deadline < today),
            })
        if logs:
            Log.create(logs)

        # Fire the flow event on parent deals before the activities disappear.
        lead_ids = self.filtered(lambda a: a.res_model == 'crm.lead').mapped('res_id')
        leads = self.env['crm.lead'].browse([i for i in lead_ids if i]).exists()
        for lead in leads:
            lead._fire_journey_event('task_completed')

        return super()._action_done(feedback=feedback, attachment_ids=attachment_ids)
