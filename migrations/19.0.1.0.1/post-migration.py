# -*- coding: utf-8 -*-
"""Repoint the hourly back-fill cron at ``_cron_evaluate_static``.

``data/ir_cron_data.xml`` is ``noupdate="1"``, so upgrading the module leaves
already-installed cron records untouched. Both scoring crons used to call
``_cron_evaluate_dynamic()``, which evaluates rules of *every* mode -- so the
hourly sweep drove the decay rules too, bleeding stalled deals ~24x faster than
the 2-day decay cadence the dynamic cron declares.
"""


def migrate(cr, version):
    cr.execute("""
        UPDATE ir_act_server s
           SET code = 'model._cron_evaluate_static()'
          FROM ir_cron c, ir_model_data d
         WHERE s.id = c.ir_actions_server_id
           AND d.model = 'ir.cron'
           AND d.res_id = c.id
           AND d.module = 'sdlc_CRM'
           AND d.name = 'cron_score_static'
           AND s.code = 'model._cron_evaluate_dynamic()'
    """)
