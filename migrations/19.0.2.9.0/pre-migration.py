# -*- coding: utf-8 -*-
"""Remove automations left behind by the scoring layer.

The scoring refactor deleted the "contact score >= 75 -> create deal" rule from
this module's XML, but a database that was installed before that still holds the
``base.automation`` row. Its filter is

    [('contact_score', '>=', 75), ('crm_handoff_done', '=', False), ...]

and ``contact_score`` no longer exists on res.partner. base_automation evaluates
that filter on EVERY write to a partner, so the row does not merely sit there
unused - it breaks ordinary work with

    ValueError: Invalid field res.partner.contact_score in condition
                ('contact_score', '>=', 75)

Seen in production on deleting a user: auth_signup writes to the user's partner
on the way out, the automation filter is evaluated, and the delete explodes.

Two passes, deliberately:

1. the rules this module is known to have dropped, by external id;
2. a sweep for ANY automation whose filter references a field that no longer
   exists on its model - so a rule that lost its external id (duplicated, or
   edited by hand) cannot keep poisoning writes.

Raw SQL throughout: the ORM would have to evaluate these very domains to load
the records, which is the thing that fails.
"""
import ast
import logging

_logger = logging.getLogger(__name__)

# Rules this module used to ship and no longer does.
STALE_XMLIDS = [
    ('sdlc_CRM', 'automation_score_handoff'),   # contact score -> create deal
    ('sdlc_CRM', 'action_score_handoff'),       # its server action
]


def _domain_fields(raw):
    """Field names referenced by a domain string, best effort."""
    if not raw:
        return set()
    try:
        domain = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return set()
    names = set()
    for leaf in domain:
        if isinstance(leaf, (list, tuple)) and len(leaf) == 3:
            expr = leaf[0]
            if isinstance(expr, str) and expr:
                # 'partner_id.name' -> the field on THIS model is partner_id
                names.add(expr.split('.')[0])
    return names


def migrate(cr, version):
    if not version:
        return

    # ---- 1. the known ones, by external id -------------------------------
    for module, name in STALE_XMLIDS:
        cr.execute("""
            SELECT model, res_id FROM ir_model_data
             WHERE module = %s AND name = %s
        """, (module, name))
        row = cr.fetchone()
        if not row:
            continue
        model, res_id = row
        table = model.replace('.', '_')
        cr.execute("SELECT to_regclass(%s)", (table,))
        if cr.fetchone()[0]:
            cr.execute('DELETE FROM "%s" WHERE id = %%s' % table, (res_id,))
            _logger.info("sdlc_CRM: deleted obsolete %s %s.%s (id=%s)",
                         model, module, name, res_id)
        cr.execute("DELETE FROM ir_model_data WHERE module = %s AND name = %s",
                   (module, name))

    # ---- 2. anything else filtering on a field that is gone ---------------
    cr.execute("""
        SELECT ba.id, m.model, ba.filter_domain, ba.filter_pre_domain
          FROM base_automation ba
          JOIN ir_model m ON m.id = ba.model_id
    """)
    for auto_id, model, domain, pre_domain in cr.fetchall():
        referenced = _domain_fields(domain) | _domain_fields(pre_domain)
        if not referenced:
            continue
        cr.execute("""
            SELECT name FROM ir_model_fields
             WHERE model = %s AND name = ANY(%s)
        """, (model, list(referenced)))
        known = {r[0] for r in cr.fetchall()}
        missing = sorted(referenced - known)
        if missing:
            # cascades to its server action via ir_act_server.base_automation_id
            cr.execute("DELETE FROM base_automation WHERE id = %s", (auto_id,))
            cr.execute("""
                DELETE FROM ir_model_data
                 WHERE model = 'base.automation' AND res_id = %s
            """, (auto_id,))
            _logger.warning(
                "sdlc_CRM: removed automation id=%s on %s - it filtered on "
                "field(s) that no longer exist: %s", auto_id, model,
                ', '.join(missing))
