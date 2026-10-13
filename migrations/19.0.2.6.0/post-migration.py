# -*- coding: utf-8 -*-
"""Retire the lead layer: everything is an opportunity.

data/crm_leads_data.xml used to imply crm.group_use_lead from base.group_user,
which gave every internal user the two-step lead -> opportunity funnel. The team
never used it, so the implication is removed here and any lead still sitting in
the database is promoted to a deal rather than being left unreachable once the
Leads menu is hidden.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    use_lead = env.ref('crm.group_use_lead', raise_if_not_found=False)
    base_user = env.ref('base.group_user', raise_if_not_found=False)
    if use_lead and base_user and use_lead in base_user.implied_ids:
        base_user.write({'implied_ids': [(3, use_lead.id)]})
        # implied_ids only governs future grants; strip the group from users too
        use_lead.write({'user_ids': [(5, 0, 0)]})

    leads = env['crm.lead'].with_context(active_test=False).search([('type', '=', 'lead')])
    for lead in leads:
        # convert_opportunity() creates/links the customer and stamps the
        # conversion date, exactly as the wizard would have done.
        lead.convert_opportunity(lead.partner_id or False)
