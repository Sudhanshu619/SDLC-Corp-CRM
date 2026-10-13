# -*- coding: utf-8 -*-
"""Share the house pipeline's stages again, and light up the native signals.

19.0.2.2.0 scoped every stage to the default pipeline so a new pipeline would
not inherit them. The cost only showed on the board: Odoo renders a stage column
when the stage is shared, belongs to the board's team, or already holds a deal
(crm.lead._read_group_stage_ids), so a scoped set collapsed the board to the
columns that happened to hold deals - with nowhere to drag a card forward.

Scoping still happens for a genuinely new sales process (crm.team.create copies
a cycle for it); the house set goes back to shared.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    teams = env['crm.team'].search([('use_opportunities', '=', True)])
    stages = env['crm.stage'].search([])

    # One sales process: its stages belong to everyone.
    if len(teams) <= 1:
        stages.write({'team_ids': [(5, 0, 0)]})

    for team in teams:
        team._ensure_single_won_stage()
        team._apply_stage_task_defaults()
