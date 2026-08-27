# -*- coding: utf-8 -*-
from odoo import fields, models


class CrmWoodpeckerSimulator(models.TransientModel):
    """Manual test harness for the Woodpecker -> CRM flow.

    Lets an admin fire an engagement event (open/click/reply/bounce) for an
    email exactly as the /sdlc_crm/woodpecker webhook would, so the full chain
    -- resolve-or-create contact -> award points -> cross threshold -> open deal
    -- can be exercised without a live Woodpecker account.
    """
    _name = 'crm.woodpecker.simulator'
    _description = 'Woodpecker Event Simulator (test)'

    email = fields.Char(required=True)
    contact_name = fields.Char('Contact Name')
    company_name = fields.Char('Company')
    campaign_name = fields.Char('Campaign', default='Cold Outreach Q3')
    event_type = fields.Selection([
        ('email_sent', 'Email Sent'),
        ('email_opened', 'Email Opened'),
        ('email_clicked', 'Email Clicked'),
        ('email_replied', 'Email Replied'),
        ('email_bounced', 'Email Bounced'),
        ('unsubscribed', 'Unsubscribed / Opted Out'),
    ], default='email_replied', required=True)
    url = fields.Char('Link (for clicks)')

    def action_log(self):
        self.ensure_one()
        event = self.env['crm.tracked.event'].sudo().record_event({
            'name': self.event_type,
            'event_type': self.event_type,
            'email': self.email,
            'contact_name': self.contact_name,
            'company_name': self.company_name,
            'campaign_name': self.campaign_name,
            'url': self.url,
        })
        partner = event.partner_id
        msg = "Logged %s for %s. Contact score: %s%s" % (
            self.event_type, self.email,
            partner.contact_score if partner else 0,
            " — deal opened (handed off)" if (partner and partner.crm_handoff_done) else "",
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Woodpecker event logged',
                'message': msg,
                'type': 'success' if (partner and partner.crm_handoff_done) else 'info',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
