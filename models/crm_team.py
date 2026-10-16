# -*- coding: utf-8 -*-
import logging

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# The stage cycle a pipeline starts with, and the task each stage schedules for
# the deal owner. Defined here rather than in a data file on purpose: a data
# file would have to <record> Odoo's own crm.stage_lead* ids, and an upgrade
# then dies with "Cannot update missing record" the moment one of them is
# deleted -- which happens as soon as someone deletes the pipeline owning it.
DEFAULT_STAGES = [
    # name, sequence, is_won, activity type xml id, task summary, days until due
    ('New', 1, False, 'mail.mail_activity_data_call', 'Qualify: discovery call', 1),
    ('Qualified', 2, False, 'mail.mail_activity_data_meeting', 'Run the discovery meeting', 2),
    ('Proposition', 3, False, 'mail.mail_activity_data_email', 'Send the proposal', 2),
    ('Negotiation', 4, False, 'mail.mail_activity_data_call', 'Agree terms and close', 3),
    ('Won', 70, True, 'mail.mail_activity_data_todo', 'Hand over to delivery', 1),
]


class CrmTeam(models.Model):
    """A pipeline.

    Odoo calls this a Sales Team and shares one global pool of stages between
    every team, so a new pipeline silently inherits whatever stages already
    exist. Here a pipeline owns its stages: creating one gives it its own cycle,
    scoped through ``crm.stage.team_ids``, so opening a pipeline shows that
    pipeline's stages and nothing else.
    """
    _inherit = 'crm.team'
    # The record stays crm.team (Odoo's pipeline carrier), but nothing in the
    # UI calls it a team: one sales team runs this CRM, and what it manages is
    # pipelines.
    _description = 'Pipeline'

    @api.model_create_multi
    def create(self, vals_list):
        teams = super().create(vals_list)
        for team in teams:
            if team.use_opportunities:
                team._create_default_stages()
        teams._sync_pipeline_menus()
        return teams

    def write(self, vals):
        res = super().write(vals)
        # a renamed pipeline has to rename its menu entry too
        if {'name', 'sequence', 'use_opportunities'} & set(vals):
            self._sync_pipeline_menus()
        return res

    def unlink(self):
        """Let a pipeline be deleted together with the stages it owns.

        ``crm.stage.team_ids`` is declared ``ondelete='restrict'``, so once a
        pipeline owns stages PostgreSQL refuses to delete it (constraint
        ``crm_stage_crm_team_rel_crm_team_id_fkey``). Clean up first:

        * a stage shipped by a module (it has an external id) is only detached,
          never deleted -- deleting it would break that module's data and every
          later upgrade that references it;
        * a stage created for this pipeline alone goes with it;
        * a stage still holding deals is kept and left unscoped.

        A pipeline that still holds deals is refused outright, with a message
        saying what to do: ``crm.lead.team_id`` is ``ondelete='set null'``, so
        deleting it would silently strand every deal with no pipeline.
        """
        Lead = self.env['crm.lead'].with_context(active_test=False).sudo()
        for team in self:
            deals = Lead.search_count([('team_id', '=', team.id)])
            if deals:
                raise UserError(_(
                    'Pipeline "%(name)s" still holds %(count)s deal(s). Move '
                    'them to another pipeline first, or archive this pipeline '
                    'instead of deleting it - archiving keeps the deals and '
                    'their history.',
                    name=team.name, count=deals))

        # the menu entry goes with the pipeline, or it points at nothing
        self._drop_pipeline_menus()

        stages = self.env['crm.stage'].sudo().search([('team_ids', 'in', self.ids)])
        for stage in stages:
            detach = [(3, team.id) for team in self]
            if stage.team_ids - self:
                stage.team_ids = detach          # shared with a pipeline that stays
            elif stage.get_external_id().get(stage.id):
                stage.team_ids = detach          # shipped by a module: keep it
            elif Lead.search_count([('stage_id', '=', stage.id)]):
                stage.team_ids = [(5, 0, 0)]     # a deal still sits in it
            else:
                stage.unlink()
        return super().unlink()

    # ------------------------------------------------------------------
    # Stage cycle
    # ------------------------------------------------------------------
    def _create_default_stages(self):
        """Give this pipeline its own stage cycle.

        The cycle is copied from the default pipeline when it has one, so a new
        pipeline starts out looking like the one the company already runs;
        otherwise the built-in cycle above is used. Either way the new stages
        are scoped to this pipeline alone.
        """
        self.ensure_one()
        Stage = self.env['crm.stage'].sudo()
        if Stage.search_count([('team_ids', 'in', self.id)]):
            return  # already has its own stages (e.g. duplicated pipeline)

        template = self.env.ref('sales_team.team_sales_department', raise_if_not_found=False)
        source = Stage.browse()
        if template and template.exists() and template != self:
            source = Stage.search([('team_ids', 'in', template.id)])

        if source:
            for stage in source:
                stage.copy({'name': stage.name, 'team_ids': [(6, 0, [self.id])]})
            return True

        for name, sequence, is_won, act_xmlid, summary, delay in DEFAULT_STAGES:
            act = self.env.ref(act_xmlid, raise_if_not_found=False)
            Stage.create({
                'name': name,
                'sequence': sequence,
                'is_won': is_won,
                'team_ids': [(6, 0, [self.id])],
                'default_activity_type_id': act.id if act else False,
                'default_activity_summary': summary,
                'default_activity_delay': delay,
            })
        return True

    @api.model
    def _ensure_default_pipeline(self):
        """Guarantee the CRM is usable: one pipeline, with a full stage cycle.

        Run at install and by the 19.0.2.2.0 migration. It repairs a database
        where the stages or the pipeline were deleted, and it is the reason the
        cycle is no longer declared in a data file.
        """
        Stage = self.env['crm.stage'].sudo()

        pipeline = self.env.ref('sales_team.team_sales_department', raise_if_not_found=False)
        if not pipeline or not pipeline.exists():
            pipeline = self.search([('use_opportunities', '=', True)], limit=1)
        if not pipeline:
            # create() builds the cycle for it
            return self.create({'name': 'Sales'})

        # Every stage BELONGS to a pipeline. Odoo's _read_group_stage_ids
        # always adds the unscoped stages (team_ids empty) to whatever board is
        # open, so one unscoped stage would surface in every pipeline - the
        # opposite of "each pipeline has its own stages". The house cycle is
        # therefore adopted by the default pipeline; a second pipeline gets its
        # own copy from create().
        pipeline._ensure_house_stages()
        pipeline._adopt_unscoped_stages()
        pipeline._ensure_single_won_stage()
        pipeline._apply_stage_task_defaults()
        pipeline._sync_pipeline_menus()
        return pipeline

    def _ensure_house_stages(self):
        """Make sure every stage of the house cycle exists, by name.

        Only the missing ones are created, so Odoo's shipped stages are kept
        (deals already sit in them) and nothing is duplicated. They are handed
        to this pipeline by _adopt_unscoped_stages immediately afterwards.
        """
        Stage = self.env['crm.stage'].sudo()
        existing = {(s.name or '').strip().lower() for s in Stage.search([])}
        created = Stage.browse()
        for name, sequence, is_won, act_xmlid, summary, delay in DEFAULT_STAGES:
            if name.strip().lower() in existing:
                continue
            act = self.env.ref(act_xmlid, raise_if_not_found=False)
            created |= Stage.create({
                'name': name,
                'sequence': sequence,
                'is_won': is_won,
                'default_activity_type_id': act.id if act else False,
                'default_activity_summary': summary,
                'default_activity_delay': delay,
            })
        if created:
            _logger.info("sdlc_CRM: added missing stage(s) %s",
                         created.mapped('name'))
        return created

    def _adopt_unscoped_stages(self):
        """Give every ownerless stage to this pipeline.

        Odoo ships New / Qualified / Proposition / Won with no team, and its
        column logic shows ownerless stages on EVERY board. Handing them to the
        default pipeline is what makes each pipeline show only its own cycle.
        """
        self.ensure_one()
        Stage = self.env['crm.stage'].sudo()
        strays = Stage.search([('team_ids', '=', False)])
        if strays:
            strays.write({'team_ids': [(4, self.id)]})
            _logger.info("sdlc_CRM: pipeline %r adopted stage(s) %s",
                         self.display_name, strays.mapped('name'))
        return strays

    # ==================================================================
    # "My Pipelines" menu - one entry per pipeline
    # ==================================================================
    def _pipeline_menu_xmlid(self):
        self.ensure_one()
        return 'sdlc_pipeline_menu_%s' % self.id

    def _pipeline_action_xmlid(self):
        self.ensure_one()
        return 'sdlc_pipeline_action_%s' % self.id

    def _sync_pipeline_menus(self):
        """Create or rename the menu entry that opens this pipeline's board.

        Odoo menus are plain records, so "a menu per pipeline" has to be built
        as pipelines are created. Each entry carries a stable external id made
        from the pipeline id - that is what lets a rename update it in place
        and a delete clean up after itself.
        """
        parent = self.env.ref('sdlc_CRM.menu_sdlc_my_pipelines',
                              raise_if_not_found=False)
        if not parent:
            return False
        IMD = self.env['ir.model.data'].sudo()
        Action = self.env['ir.actions.act_window'].sudo()
        Menu = self.env['ir.ui.menu'].sudo()

        for team in self.sudo():
            if not team.use_opportunities:
                continue
            act_vals = {
                'name': team.name,
                'res_model': 'crm.lead',
                'view_mode': 'kanban,list,form,pivot,graph,activity',
                'domain': "[('type', '=', 'opportunity'), ('team_id', '=', %s)]" % team.id,
                # default_team_id is what makes Odoo render THIS pipeline's
                # stage columns, the empty ones included.
                'context': ("{'default_type': 'opportunity', 'default_team_id': %s}"
                            % team.id),
            }
            action = self.env.ref('sdlc_CRM.%s' % team._pipeline_action_xmlid(),
                                  raise_if_not_found=False)
            if action:
                action.sudo().write(act_vals)
            else:
                action = Action.create(act_vals)
                IMD.create({
                    'module': 'sdlc_CRM',
                    'name': team._pipeline_action_xmlid(),
                    'model': 'ir.actions.act_window',
                    'res_id': action.id,
                    'noupdate': True,
                })

            menu_vals = {
                'name': team.name,
                'parent_id': parent.id,
                'action': 'ir.actions.act_window,%s' % action.id,
                # 'All Pipelines' holds sequence 1; keep the per-pipeline
                # entries below it whatever the pipeline's own sequence is.
                'sequence': 10 + (team.sequence or 0),
            }
            menu = self.env.ref('sdlc_CRM.%s' % team._pipeline_menu_xmlid(),
                                raise_if_not_found=False)
            if menu:
                menu.sudo().write(menu_vals)
            else:
                menu = Menu.create(menu_vals)
                IMD.create({
                    'module': 'sdlc_CRM',
                    'name': team._pipeline_menu_xmlid(),
                    'model': 'ir.ui.menu',
                    'res_id': menu.id,
                    'noupdate': True,
                })
        return True

    def _drop_pipeline_menus(self):
        """Remove the menu entry and action belonging to these pipelines."""
        for team in self.sudo():
            for xmlid in (team._pipeline_menu_xmlid(),
                          team._pipeline_action_xmlid()):
                rec = self.env.ref('sdlc_CRM.%s' % xmlid, raise_if_not_found=False)
                if rec:
                    rec.sudo().unlink()
                self.env['ir.model.data'].sudo().search([
                    ('module', '=', 'sdlc_CRM'), ('name', '=', xmlid)]).unlink()
        return True

    @api.model
    def _rebuild_pipeline_menus(self):
        """Rebuild every pipeline's menu entry - install and migration entry."""
        teams = self.search([('use_opportunities', '=', True)])
        return teams._sync_pipeline_menus()

    def _ensure_single_won_stage(self):
        """Exactly one won stage per pipeline.

        ``action_set_won`` picks a won stage by sequence; two of them (easy to
        end up with once stages are copied per pipeline) makes the Won button
        land somewhere unexpected.
        """
        Stage = self.env['crm.stage'].sudo()
        for team in self:
            won = Stage.search([
                ('is_won', '=', True),
                '|', ('team_ids', '=', False), ('team_ids', 'in', team.id),
            ], order='sequence desc')
            for extra in won[1:]:
                extra.is_won = False
        return True

    def _apply_stage_task_defaults(self):
        """Fill in the stage task config for stages that have none.

        Matched by name, so Odoo's shipped stages (New / Qualified /
        Proposition / Won) pick up a task without being replaced. Never
        overwrites a stage someone has already configured.
        """
        Stage = self.env['crm.stage'].sudo()
        by_name = {name: (xmlid, summary, delay)
                   for name, _seq, _won, xmlid, summary, delay in DEFAULT_STAGES}
        for team in self:
            # The house cycle is deliberately unscoped (team_ids empty), so a
            # team_ids-only domain matched nothing and no stage ever got its
            # task configured on a fresh install.
            for stage in Stage.search(
                    ['|', ('team_ids', '=', False), ('team_ids', 'in', team.id)]):
                if stage.default_activity_type_id or stage.name not in by_name:
                    continue
                xmlid, summary, delay = by_name[stage.name]
                act = self.env.ref(xmlid, raise_if_not_found=False)
                if act:
                    stage.write({
                        'default_activity_type_id': act.id,
                        'default_activity_summary': summary,
                        'default_activity_delay': delay,
                    })
        # Native stagnation signal: a deal sitting in a stage longer than this
        # is flagged "rotting" and can be filtered on. This is what replaces the
        # decay scoring the module used to carry.
        for stage in Stage.search([('rotting_threshold_days', '=', 0)]):
            stage.rotting_threshold_days = 30 if stage.is_won else 14
        return True
