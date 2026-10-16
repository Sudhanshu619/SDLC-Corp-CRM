# -*- coding: utf-8 -*-
"""Pipelines become the grouping, and each one owns its stages.

Campaigns are no longer how deals are grouped - a pipeline is. Two things have
to happen on a database that predates the change:

* stages with no owner are handed to the default pipeline. Odoo's column logic
  (crm.lead._read_group_stage_ids) always adds ownerless stages to whatever
  board is open, so a single unscoped stage would appear in every pipeline;
* every pipeline gets its entry under "My Pipelines" - those menus are records
  built in code, so an existing database has none yet.

The campaign field itself is left alone. It is Odoo's own utm.campaign and
costs nothing; it simply no longer appears in this module's screens.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['crm.team']._ensure_default_pipeline()
    env['crm.team']._rebuild_pipeline_menus()

    teams = env['crm.team'].sudo().search([('use_opportunities', '=', True)])
    strays = env['crm.stage'].sudo().search_count([('team_ids', '=', False)])
    _logger.info("sdlc_CRM: %d pipeline(s) now carry their own stages; "
                 "%d ownerless stage(s) remain", len(teams), strays)
