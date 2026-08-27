# -*- coding: utf-8 -*-
{
    'name': 'SDLC Sales CRM (ActiveCampaign-style)',
    'version': '19.0.1.0.0',
    'category': 'Sales/CRM',
    'summary': 'ActiveCampaign-style Sales CRM: multi-contact deals, decay scoring, '
               'task outcomes & sentiment, self-propelling stage->task loop, behavioural '
               'tracking and the score->deal handoff.',
    'description': """
SDLC Sales CRM
==============
A faithful rebuild of ActiveCampaign's "Deals CRM" flow on top of Odoo 19 Community.

What this module adds on top of native ``crm``:

* **Core** - multi-contact deals (primary + secondary), per-deal currency with a
  company-currency roll-up for reporting, account resolution, a decoupled AC-style
  deal status, stage-entry history for velocity reporting, and a journey-event audit
  trail that makes every flow observable.
* **Activity** - task outcomes with sentiment, an ``outcome_id`` on ``mail.activity``,
  and a permanent completion log (``crm.activity.log``) that survives activity deletion
  so the Task Overview report is computable. Native activity chaining wires the
  self-propelling stage->task loop.
* **Scoring** - contact & deal score models built from segment rules, an immutable
  points ledger, point expiry, decay crons and the score->deal marketing/sales handoff.
* **Tracking** - a custom behavioural event store plus a REST ingest endpoint, feeding
  behavioural score rules and automation triggers.
* **Flows** - the AC flows (handoff, stage->task loop, decay) shipped as ready-made
  ``base.automation`` rules and server actions.
* **Reports** - pivots/graphs for Deal Overview, Task Overview and Stage Velocity.

See ``README.md`` for the flow-by-flow mapping to the blueprint.
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
        'utm',
        'board',
    ],
    'data': [
        'security/sdlc_crm_security.xml',
        'security/ir.model.access.csv',
        'data/crm_activity_type_data.xml',
        'data/crm_activity_outcome_data.xml',
        'data/crm_score_data.xml',
        'data/ir_cron_data.xml',
        'data/base_automation_data.xml',
        'data/sdlc_crm_admin.xml',
        'views/crm_lead_views.xml',
        'views/crm_stage_views.xml',
        'views/crm_activity_outcome_views.xml',
        'views/crm_activity_log_views.xml',
        'views/crm_score_views.xml',
        'views/crm_tracked_event_views.xml',
        'views/crm_stage_history_views.xml',
        'views/crm_journey_event_views.xml',
        'views/res_partner_views.xml',
        'views/sdlc_crm_dashboard.xml',
        'views/sdlc_crm_menus.xml',
        'views/crm_woodpecker_views.xml',
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
    'application': True,
    'installable': True,
}
