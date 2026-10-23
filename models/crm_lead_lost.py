# -*- coding: utf-8 -*-
from odoo import fields, models


class CrmLeadLost(models.TransientModel):
    """The Mark Lost popup: capture the loss-analysis details in the same step
    as the reason, while the rep still remembers them."""
    _inherit = 'crm.lead.lost'

    loss_category = fields.Selection(related='lost_reason_id.loss_category')
    lost_competitor = fields.Char('Lost To (Competitor)')
    lost_reengage_date = fields.Date('Re-engage On')

    def action_lost_reason_apply(self):
        self.ensure_one()
        vals = {}
        if self.lost_competitor:
            vals['lost_competitor'] = self.lost_competitor
        if self.lost_reengage_date:
            vals['lost_reengage_date'] = self.lost_reengage_date
        if vals:
            self.lead_ids.write(vals)
        return super().action_lost_reason_apply()
