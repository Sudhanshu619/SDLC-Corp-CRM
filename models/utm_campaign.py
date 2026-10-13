# -*- coding: utf-8 -*-
from odoo import _, api, fields, models


class UtmCampaign(models.Model):
    """A campaign groups deals - nothing more.

    Every deal in every campaign moves through the one shared stage cycle.
    Campaigns used to be able to own their stages; that only ever produced
    duplicate columns with the same names, so the pipeline looked identical
    whichever campaign you opened. Stages are configured in one place now:
    Configuration > Pipeline Setup.
    """
    _inherit = 'utm.campaign'

    deal_count = fields.Integer(compute='_compute_deal_count')

    def _compute_deal_count(self):
        counts = {}
        if self.ids:
            groups = self.env['crm.lead']._read_group(
                [('campaign_id', 'in', self.ids), ('type', '=', 'opportunity')],
                ['campaign_id'], ['__count'])
            counts = {campaign.id: count for campaign, count in groups}
        for campaign in self:
            campaign.deal_count = counts.get(campaign.id, 0)

    def action_view_deals(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("%s deals", self.name),
            'res_model': 'crm.lead',
            'domain': [('type', '=', 'opportunity'), ('campaign_id', '=', self.id)],
            'context': {'default_campaign_id': self.id, 'default_type': 'opportunity'},
            'view_mode': 'kanban,list,form',
        }
