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
        return teams

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

        # Stages are left UNSCOPED on purpose. Odoo only renders a stage column
        # when the stage is shared (team_ids empty), belongs to the board's
        # team_id, or already holds a deal (crm.lead._read_group_stage_ids), so
        # scoping every stage to the default pipeline made a fresh board show
        # one column and no way to drag a deal forward. Scoping is still what
        # happens when someone creates a *second* sales process - that one gets
        # its own cycle - but the house pipeline keeps the shared set.
        # Odoo's crm module already ships New / Qualified / Proposition / Won,
        # so "create the cycle only when no stage exists" never fired on a fresh
        # install - and Negotiation was simply missing. Complete the cycle
        # instead: add what the house cycle needs and nothing else.
        pipeline._ensure_house_stages()
        pipeline._ensure_single_won_stage()

        pipeline._apply_stage_task_defaults()
        return pipeline

    def _ensure_house_stages(self):
        """Make sure every stage of the house cycle exists, by name.

        Only the missing ones are created, so Odoo's shipped stages are kept
        (deals already sit in them) and nothing is duplicated. New stages are
        left unscoped, like the rest of the house cycle - see the note in
        _ensure_default_pipeline.
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
