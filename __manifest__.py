# -*- coding: utf-8 -*-
{
    'name': 'SDLC Sales CRM (ActiveCampaign-style)',
    'version': '19.0.2.9.0',
    'category': 'Sales/CRM',
    'summary': 'Sales CRM: leads and multi-contact deals, per-deal currency, stage '
               'history and velocity, task outcomes & sentiment, and a '
               'self-propelling stage->task loop.',
    'description': """
SDLC Sales CRM
==============
A sales CRM built on native ``crm``, with deals at the centre.

* **Pipeline** - Odoo's Leads layer is enabled, so a raw prospect lives on the
  lead itself (name, email, phone) and no contact record exists until the lead is
  converted into a deal. A pipeline is a Sales Team; stages can be shared or
  scoped to one pipeline via ``crm.stage.team_ids``.
* **Deals** - multi-contact deals (primary + secondary), per-deal currency with a
  company-currency roll-up for reporting, account resolution, an explicit
  Open/Won/Lost status, stage-entry history for velocity reporting, and a
  journey-event audit trail that makes every transition observable.
* **Activity** - task outcomes with sentiment, an ``outcome_id`` on
  ``mail.activity``, and a permanent completion log (``crm.activity.log``) that
  survives activity deletion so the Task Overview report is computable.
* **Flows** - entering a stage creates that stage's task for the deal owner;
  winning a deal tags every contact on it as a customer.
* **Reports** - a KPI dashboard plus Task Overview, Stage Velocity, Journey
  Events and Lost Deals.

Deal priority comes from Odoo's native star priority and probability; this module
ships no scoring engine, no behavioural tracking and no email-outreach
integration.
""",
    'author': 'SDLC Corp',
    'website': 'https://www.sdlccorp.com',
    'license': 'LGPL-3',
    'depends': [
        'crm',
        'mail',
        'sales_team',
        'contacts',
        'base_automation',
    ],
    'data': [
        'security/sdlc_crm_security.xml',
        'security/ir.model.access.csv',
        'data/crm_activity_type_data.xml',
        'data/crm_activity_outcome_data.xml',
        'data/crm_team_data.xml',
        'data/crm_campaign_data.xml',
        'data/crm_leads_data.xml',
        'data/base_automation_data.xml',
        'data/sdlc_crm_admin.xml',
        'data/sdlc_crm_lockdown.xml',
        'views/crm_lead_views.xml',
        'views/res_partner_views.xml',
        'views/crm_team_views.xml',
        'views/crm_stage_views.xml',
        'views/crm_activity_outcome_views.xml',
        'views/crm_activity_log_views.xml',
        'views/crm_stage_history_views.xml',
        'views/crm_journey_event_views.xml',
        'views/sdlc_crm_dashboard.xml',
        'views/sdlc_crm_menus.xml',
    ],
    'demo': [
        'demo/sdlc_crm_demo.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'sdlc_CRM/static/src/dashboard/dashboard.scss',
            'sdlc_CRM/static/src/dashboard/dashboard.js',
            'sdlc_CRM/static/src/dashboard/dashboard.xml',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'application': True,
    'installable': True,
}
