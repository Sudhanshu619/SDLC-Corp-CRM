# -*- coding: utf-8 -*-
import logging
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.tools.safe_eval import safe_eval, datetime as safe_datetime, dateutil as safe_dateutil

_logger = logging.getLogger(__name__)

# Which stored field on the subject model holds the rolled-up total.
SUBJECT_TOTAL_FIELD = {
    'res.partner': 'contact_score',
    'crm.lead': 'deal_score',
}


class CrmScore(models.Model):
    """A named score model over Contacts or Deals.

    Unlimited named scores per database (AC: one per product line). Built from
    1..n rules, each a segment condition + points, with optional per-score
    point expiry (blueprint A4.8).
    """
    _name = 'crm.score'
    _description = 'Score Model (Contact or Deal)'
    _order = 'name'

    name = fields.Char(required=True)
    subject = fields.Selection(
        [('res.partner', 'Contact'), ('crm.lead', 'Deal')],
        required=True, default='res.partner')
    active = fields.Boolean(default=True)
    expiry_days = fields.Integer(
        string='Points Expire After (days)',
        help="If set, awarded points expire this many days after being granted. "
             "0 = never expire.")
    rule_ids = fields.One2many('crm.score.rule', 'score_id', string='Rules')
    ledger_ids = fields.One2many('crm.score.ledger', 'score_id', string='Ledger')
    rule_count = fields.Integer(compute='_compute_counts')
    ledger_count = fields.Integer(compute='_compute_counts')

    @api.depends('rule_ids', 'ledger_ids')
    def _compute_counts(self):
        for score in self:
            score.rule_count = len(score.rule_ids)
            score.ledger_count = len(score.ledger_ids)

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------
    def _award(self, rule, records):
        """Create ledger rows for ``records`` matched by ``rule``."""
        if not records:
            return
        Ledger = self.env['crm.score.ledger'].sudo()
        expiry = False
        if self.expiry_days:
            expiry = fields.Datetime.now() + relativedelta(days=self.expiry_days)
        Ledger.create([{
            'score_id': self.id,
            'rule_id': rule.id,
            'res_model': self.subject,
            'res_id': rec.id,
            'points': rule.points,
            'reason': rule.name,
            'date_expiry': expiry,
        } for rec in records])

    def _evaluate(self, records=None):
        """Idempotent for 'once' rules, additive for 'every' rules.

        Blueprint E3: AC only supports 'once' and forces one automation per
        repeatable rule; our ``evaluation_mode`` removes that limitation.
        """
        for score in self:
            Model = self.env[score.subject].sudo()
            base = records.filtered(lambda r: r._name == score.subject) if records else Model.search([])
            Ledger = self.env['crm.score.ledger'].sudo()
            eval_ctx = {
                'datetime': safe_datetime,
                'dateutil': safe_dateutil,
                'relativedelta': relativedelta,
                'context_today': fields.Date.context_today,
                'uid': self.env.uid,
            }
            for rule in score.rule_ids.filtered('active'):
                try:
                    domain = safe_eval(rule.domain or '[]', eval_ctx)
                except Exception:
                    _logger.warning("SDLC CRM score rule %s has an invalid domain", rule.name)
                    continue
                matched = base.filtered_domain(domain)
                if not matched:
                    continue
                if rule.evaluation_mode == 'once':
                    already = Ledger.search([
                        ('rule_id', '=', rule.id),
                        ('res_id', 'in', matched.ids),
                    ]).mapped('res_id')
                    matched = matched.filtered(lambda r: r.id not in already)
                score._award(rule, matched)
            score._recompute_totals(base)
        return True

    def _recompute_totals(self, records=None):
        """Roll live (non-expired) ledger points onto the subject's total field."""
        for score in self:
            total_field = SUBJECT_TOTAL_FIELD.get(score.subject)
            if not total_field:
                continue
            Model = self.env[score.subject].sudo()
            targets = records if records else Model.search([])
            now = fields.Datetime.now()
            # Aggregate across *all* score models for this subject, so the total
            # field reflects every score, not just this one.
            for rec in targets:
                rows = self.env['crm.score.ledger'].sudo().search([
                    ('res_model', '=', score.subject),
                    ('res_id', '=', rec.id),
                    '|', ('date_expiry', '=', False), ('date_expiry', '>', now),
                ])
                total = sum(rows.mapped('points'))
                if rec[total_field] != total:
                    rec.sudo().write({total_field: total})
        return True

    def action_recompute_all(self):
        """Manual 'recompute all' button (blueprint B3 recommendation)."""
        self._evaluate()
        return True

    # ------------------------------------------------------------------
    # Crons
    # ------------------------------------------------------------------
    @api.model
    def _cron_evaluate_dynamic(self):
        """Run 'every'-mode rules (e.g. decay) on their cadence."""
        scores = self.search([('active', '=', True)])
        scores._evaluate()

    @api.model
    def _cron_expire_points(self):
        """Drop expired ledger influence by recomputing totals. Rows are kept
        for audit; expiry is applied at read time by the >now filter."""
        for subject in SUBJECT_TOTAL_FIELD:
            scores = self.search([('active', '=', True), ('subject', '=', subject)], limit=1)
            if scores:
                scores._recompute_totals()


class CrmScoreRule(models.Model):
    """One scoring rule: a segment condition (the single condition language)
    plus points. Points may be negative (decay)."""
    _name = 'crm.score.rule'
    _description = 'Score Rule'
    _order = 'sequence, id'

    score_id = fields.Many2one('crm.score', required=True, ondelete='cascade')
    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    subject = fields.Selection(related='score_id.subject', store=True, readonly=True)
    domain = fields.Char(
        required=True, default='[]',
        help="The one condition language. A record matching this domain is "
             "awarded the rule's points.")
    points = fields.Integer(required=True, help="May be negative for decay rules.")
    evaluation_mode = fields.Selection([
        ('once', 'Award once per record'),
        ('every', 'Award on every match (repeatable)'),
    ], default='once', required=True,
        help="'once' = award a record at most once ever (AC's only mode). "
             "'every' = award again on each cron pass while the condition holds "
             "(used for decay).")
    active = fields.Boolean(default=True)


class CrmScoreLedger(models.Model):
    """Immutable audit trail: every point ever awarded, and why. A stored total
    with no audit trail is unsupportable in production (blueprint E3)."""
    _name = 'crm.score.ledger'
    _description = 'Score Ledger Entry'
    _order = 'date_award desc, id desc'

    score_id = fields.Many2one('crm.score', required=True, ondelete='cascade', index=True)
    rule_id = fields.Many2one('crm.score.rule', ondelete='set null', index=True)
    res_model = fields.Char(required=True, index=True)
    res_id = fields.Many2oneReference('Record', model_field='res_model', required=True, index=True)
    points = fields.Integer(required=True)
    reason = fields.Char()
    date_award = fields.Datetime(default=lambda s: fields.Datetime.now(), index=True)
    date_expiry = fields.Datetime(index=True)
    expired = fields.Boolean(compute='_compute_expired', search='_search_expired')

    @api.depends('date_expiry')
    def _compute_expired(self):
        now = fields.Datetime.now()
        for row in self:
            row.expired = bool(row.date_expiry and row.date_expiry <= now)

    def _search_expired(self, operator, value):
        now = fields.Datetime.now()
        expired_domain = [('date_expiry', '!=', False), ('date_expiry', '<=', now)]
        if (operator == '=' and value) or (operator == '!=' and not value):
            return expired_domain
        return ['|', ('date_expiry', '=', False), ('date_expiry', '>', now)]
