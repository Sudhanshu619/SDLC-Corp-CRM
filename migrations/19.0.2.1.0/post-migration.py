# -*- coding: utf-8 -*-
"""Scope the shipped stages to the default pipeline.

``data/crm_stage_data.xml`` is ``noupdate="1"``, so the ``team_ids`` added there
never reaches databases installed before this version. A stage with no pipeline
is shared by every pipeline, which would make each new pipeline inherit the
default cycle instead of owning one -- so pin the five shipped stages to the
default pipeline, but only while nobody has scoped them by hand.
"""

SHIPPED = ['crm.stage_lead1', 'crm.stage_lead2', 'crm.stage_lead3',
           'sdlc_CRM.stage_negotiation', 'crm.stage_lead4']


def migrate(cr, version):
    cr.execute("""
        SELECT res_id FROM ir_model_data
         WHERE model = 'crm.team' AND module = 'sales_team'
           AND name = 'team_sales_department'
    """)
    row = cr.fetchone()
    if not row:
        return
    team_id = row[0]

    for xmlid in SHIPPED:
        module, name = xmlid.split('.')
        cr.execute("""
            SELECT res_id FROM ir_model_data
             WHERE model = 'crm.stage' AND module = %s AND name = %s
        """, (module, name))
        stage = cr.fetchone()
        if not stage:
            continue
        stage_id = stage[0]
        cr.execute("""
            SELECT 1 FROM crm_stage_crm_team_rel WHERE crm_stage_id = %s
        """, (stage_id,))
        if cr.fetchone():
            continue  # already scoped by hand: leave it alone
        cr.execute("""
            INSERT INTO crm_stage_crm_team_rel (crm_stage_id, crm_team_id)
            VALUES (%s, %s) ON CONFLICT DO NOTHING
        """, (stage_id, team_id))
