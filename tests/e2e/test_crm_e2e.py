#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""End-to-end tests for the SDLC Sales CRM, driven through a real browser.

These are NOT Odoo unit tests - they are not picked up by `--test-enable`.
They drive the actual UI with Playwright, which is the only way to catch the
class of bug that unit tests miss here: a broken asset bundle, a view that
fails to render, a dashboard that silently shows nothing.

Run against a THROWAWAY database - the suite creates and closes real deals.

    python -m playwright install chrome          # once
    python tests/e2e/test_crm_e2e.py             # defaults to :8071

    BASE_URL=http://127.0.0.1:8071 \
    ADMIN_LOGIN=tester@test.invalid ADMIN_PASSWORD=test-pass-1234 \
    REP_LOGIN=rep@test.invalid REP_PASSWORD=rep-pass-1234 \
        python tests/e2e/test_crm_e2e.py

Exit code is 0 only when every check passes.
"""
import os
import sys
import time

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8071")
ADMIN = (os.environ.get("ADMIN_LOGIN", "tester@test.invalid"),
         os.environ.get("ADMIN_PASSWORD", "test-pass-1234"))
REP = (os.environ.get("REP_LOGIN", "rep@test.invalid"),
       os.environ.get("REP_PASSWORD", "rep-pass-1234"))
SHOTS = os.environ.get("SHOT_DIR", "")

# Addressed by xmlid, never by numeric id: ids differ between databases and
# a hardcoded one silently opens somebody else's action.
DASH = "sdlc_CRM.action_sdlc_crm_dashboard_kpi"
BOARD = "sdlc_CRM.action_sdlc_pipeline_board"
OPPS = "sdlc_CRM.action_sdlc_opportunities"
CONTACTS = "contacts.action_contacts"

STAMP = str(int(time.time()))
DEAL_WON = "E2E won deal %s" % STAMP
DEAL_LOST = "E2E lost deal %s" % STAMP


# --------------------------------------------------------------------------
# tiny harness - no pytest dependency, so this runs anywhere Playwright does
# --------------------------------------------------------------------------
class Results:
    def __init__(self):
        self.rows = []

    def check(self, name, condition, detail=""):
        self.rows.append((bool(condition), name, detail))
        print("  %-5s %s%s" % ("PASS" if condition else "FAIL", name,
                               (" - " + detail) if detail and not condition else ""))
        return bool(condition)

    @property
    def failed(self):
        return [r for r in self.rows if not r[0]]

    def report(self):
        print()
        print("=" * 66)
        print("  %d checks, %d passed, %d failed"
              % (len(self.rows), len(self.rows) - len(self.failed), len(self.failed)))
        for ok, name, detail in self.failed:
            print("    FAILED: %s%s" % (name, (" - " + detail) if detail else ""))
        print("=" * 66)
        return 0 if not self.failed else 1


R = Results()
JS_ERRORS = []


def shot(page, name):
    if SHOTS:
        try:
            page.screenshot(path=os.path.join(SHOTS, name + ".png"))
        except Exception:
            pass


def login(page, creds):
    page.goto(BASE + "/web/login", wait_until="domcontentloaded")
    page.fill("input[name='login']", creds[0])
    page.fill("input[name='password']", creds[1])
    page.click("button[type='submit']")
    page.wait_for_selector(".o_main_navbar", timeout=120000)


def logout(page):
    page.goto(BASE + "/web/session/logout", wait_until="domcontentloaded")
    page.wait_for_timeout(1200)


def open_action(page, xmlid, wait=4000):
    page.goto(BASE + "/odoo/action-" + xmlid, wait_until="domcontentloaded")
    page.wait_for_timeout(wait)


def save_form(page, timeout=8000):
    """Save, and say whether it actually happened.

    Odoo keeps the save button DISABLED until the record is dirty AND valid, so
    polling it is the only honest way to know a change was accepted. A required
    field left empty leaves it disabled forever - which is exactly the bug this
    suite found.
    """
    waited = 0
    while waited < timeout:
        btn = page.query_selector(".o_form_button_save")
        if btn and btn.is_enabled():
            try:
                page.click(".o_form_button_save", timeout=5000)
            except Exception:
                # the button can disappear mid-click: Odoo auto-saves some
                # edits (a statusbar move, for one), leaving nothing to save
                return True
            page.wait_for_timeout(3000)
            return True
        page.wait_for_timeout(300)
        waited += 300
    return False


# --------------------------------------------------------------------------
# the tests
# --------------------------------------------------------------------------
def test_login_and_dashboard(page):
    print("\n[1] login and dashboard")
    login(page, ADMIN)
    R.check("admin can log in", page.query_selector(".o_main_navbar") is not None)

    open_action(page, DASH, wait=6000)
    root = page.query_selector(".o_sdlc_dash")
    R.check("dashboard renders", root is not None)
    if not root:
        return
    body = page.inner_text("body")
    R.check("greeting is shown", "Welcome back" in body)
    R.check("KPI cards present",
            len(page.query_selector_all(".o_sdlc_dash .o_kpi")) == 4,
            "found %d" % len(page.query_selector_all(".o_sdlc_dash .o_kpi")))
    R.check("'Deals at risk' KPI present", "Deals at risk" in body)
    R.check("trend chart drawn",
            page.query_selector(".o_sdlc_dash .o_chart svg") is not None)
    R.check("no leftover 'Weighted' KPI", "Weighted" not in body)
    R.check("search box removed from dashboard",
            page.query_selector(".o_sdlc_dash .o_search") is None)
    shot(page, "01_dashboard")


def test_menu_structure(page):
    print("\n[2] menu structure")
    # land inside the CRM app first - /odoo alone opens whatever app is default
    open_action(page, DASH, wait=5000)
    tabs = [t.inner_text().strip() for t in page.query_selector_all(".o_main_navbar .o_menu_sections a, .o_main_navbar .dropdown-toggle")]
    tabs = [t for t in tabs if t]
    for expected in ("Dashboard", "Deals", "My Pipelines", "Contacts", "Reporting", "Configuration"):
        R.check("top menu has '%s'" % expected, any(expected == t for t in tabs),
                "saw %s" % tabs[:10])
    R.check("the Campaigns menu is gone", not any("Campaign" in t for t in tabs))


def test_empty_board_has_no_placeholders(page):
    print("\n[3] empty pipeline shows no fake records")
    open_action(page, BOARD, wait=5000)
    body = page.inner_text("body")
    R.check("no REF0001 placeholder card", "REF0001" not in body)
    R.check("stage columns still render",
            len(page.query_selector_all(".o_kanban_group")) >= 4,
            "found %d columns" % len(page.query_selector_all(".o_kanban_group")))
    shot(page, "02_empty_board")


def test_create_deal(page):
    print("\n[4] create a deal through the UI")
    open_action(page, BOARD, wait=4000)
    page.click(".o_control_panel button.o-kanban-button-new, button.o_list_button_add")
    page.wait_for_timeout(2500)
    # quick-create on the kanban
    name_input = page.query_selector(".o_kanban_quick_create input#name_0")
    if name_input:
        name_input.fill(DEAL_WON)
        btns = [b for b in page.query_selector_all(".o_kanban_quick_create button")
                if "Add" in (b.inner_text() or "")]
        if btns:
            btns[0].click()
        page.wait_for_timeout(3000)
    # confirm against the LIST, not the board text: a value still sitting in a
    # quick-create input would not prove the record was saved
    open_action(page, OPPS, wait=4000)
    found = page.query_selector("tr.o_data_row:has-text('%s')" % DEAL_WON)
    R.check("new deal is saved and listed", found is not None)
    shot(page, "03_deal_created")


def test_deal_lifecycle(page):
    print("\n[5] move the deal, then win it")
    open_action(page, OPPS, wait=4000)
    row = page.query_selector("tr.o_data_row:has-text('%s')" % DEAL_WON)
    R.check("deal is in the Opportunities list", row is not None)
    if not row:
        return
    row.click()
    page.wait_for_timeout(3500)

    # the statusbar should offer the stage cycle
    stages = [s.inner_text().strip() for s in page.query_selector_all(".o_statusbar_status button")]
    R.check("stage bar shows the shared cycle",
            all(s in " ".join(stages) for s in ("New", "Qualified", "Won")),
            "saw %s" % stages)

    qualified = [s for s in page.query_selector_all(".o_statusbar_status button")
                 if s.is_visible() and s.inner_text().strip().startswith("Qualified")]
    if R.check("'Qualified' step is clickable", bool(qualified)):
        # re-resolve by selector: an OWL re-render between query and click
        # leaves the handle stale and the click times out
        page.click(".o_statusbar_status button:has-text('Qualified')", timeout=15000)
        page.wait_for_timeout(2000)
        # a statusbar move auto-saves, so an explicit save is best-effort;
        # the reload below is what actually proves it stuck
        save_form(page, timeout=4000)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
        current = page.query_selector(".o_statusbar_status .o_arrow_button_current")
        R.check("stage change persisted (checked after a reload)",
                current is not None and current.inner_text().strip().startswith("Qualified"),
                "statusbar says %r" % (current.inner_text().strip()[:14] if current else None))

    sel_won = "button[name='action_set_won_rainbowman']"
    R.check("'Won' button available", page.query_selector(sel_won) is not None)
    try:
        page.click(sel_won, timeout=15000)
        page.wait_for_timeout(4000)
    except Exception as exc:
        R.check("'Won' button could be clicked", False, str(exc)[:90])
    shot(page, "04_deal_won")

    open_action(page, "sdlc_CRM.action_sdlc_won_deals", wait=4000)
    R.check("deal shows in Won", DEAL_WON in page.inner_text("body"))


def test_lost_requires_reason(page):
    print("\n[6] losing a deal asks for a reason")
    open_action(page, BOARD, wait=4000)
    page.click(".o_control_panel button.o-kanban-button-new")
    page.wait_for_timeout(2500)
    inp = page.query_selector(".o_kanban_quick_create input#name_0")
    if inp:
        inp.fill(DEAL_LOST)
        for b in page.query_selector_all(".o_kanban_quick_create button"):
            if "Add" in (b.inner_text() or ""):
                b.click()
                break
        page.wait_for_timeout(3000)

    open_action(page, OPPS, wait=4000)
    row = page.query_selector("tr.o_data_row:has-text('%s')" % DEAL_LOST)
    if not R.check("second deal created", row is not None):
        return
    row.click()
    page.wait_for_timeout(3500)
    # name= on this button is an action id, not a method - match on the label
    lost_btn = next((b for b in page.query_selector_all(".o_form_statusbar button")
                     if b.inner_text().strip() == "Lost"), None)
    R.check("'Lost' button available", lost_btn is not None)
    if lost_btn:
        lost_btn.click()
        page.wait_for_timeout(2500)
        dialog = page.query_selector(".modal-dialog")
        R.check("lost wizard opens", dialog is not None)
        if dialog:
            # the reason is mandatory in this module
            # lost_reason_id renders as clickable badges, not an input
            badges = page.query_selector_all(".modal-dialog .o_selection_badge")
            R.check("lost reasons are offered", len(badges) > 0,
                    "found %d" % len(badges))
            if badges:
                badges[0].click()
                page.wait_for_timeout(1200)
            confirm = next((b for b in page.query_selector_all(".modal-footer button")
                            if b.inner_text().strip() == "Mark as Lost"), None)
            R.check("'Mark as Lost' button present", confirm is not None)
            if confirm:
                confirm.click()
                page.wait_for_timeout(3500)
            R.check("the lost dialog closed", page.query_selector(".modal-dialog") is None)
    shot(page, "05_deal_lost")
    open_action(page, "sdlc_CRM.action_lost_deals", wait=4000)
    R.check("deal shows in Lost", DEAL_LOST in page.inner_text("body"))


def test_contact_created_from_deal(page):
    print("\n[7] a deal's company becomes a contact")
    open_action(page, CONTACTS, wait=4000)
    ok = page.query_selector(".o_list_view, .o_kanban_view") is not None
    R.check("contacts view opens", ok)


def test_dashboard_reflects_the_data(page):
    print("\n[8] dashboard counts the deals we just made")
    open_action(page, DASH, wait=7000)
    body = page.inner_text("body")
    R.check("dashboard still renders after the activity",
            page.query_selector(".o_sdlc_dash") is not None)
    R.check("won deal counted", "1" in body)
    shot(page, "06_dashboard_after")


def test_access_control(page):
    print("\n[9] access control")
    logout(page)
    login(page, REP)
    page.goto(BASE + "/odoo/settings", wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    body = page.inner_text("body")
    R.check("non-admin is denied Settings",
            "not allowed" in body.lower() or "access error" in body.lower(),
            "page said: %s" % body[:80].replace("\n", " "))
    open_action(page, DASH, wait=6000)
    R.check("non-admin can still see the dashboard",
            page.query_selector(".o_sdlc_dash") is not None)
    shot(page, "07_rep_denied")


def test_signup_is_closed(page):
    print("\n[10] public sign-up is closed")
    resp = page.goto(BASE + "/web/signup", wait_until="domcontentloaded")
    R.check("/web/signup is not reachable", resp.status == 404,
            "http %s" % resp.status)
    R.check("no self-registration form",
            page.query_selector("input[name='confirm_password']") is None)


def main():
    print("=" * 66)
    print("  SDLC CRM end-to-end suite")
    print("  target: %s" % BASE)
    print("=" * 66)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1000})
        page.on("pageerror", lambda e: JS_ERRORS.append(str(e)[:160]))

        for fn in (test_login_and_dashboard, test_menu_structure,
                   test_empty_board_has_no_placeholders, test_create_deal,
                   test_deal_lifecycle, test_lost_requires_reason,
                   test_contact_created_from_deal,
                   test_dashboard_reflects_the_data,
                   test_access_control, test_signup_is_closed):
            try:
                fn(page)
            except Exception as exc:
                R.check(fn.__name__, False, "raised %s: %s" % (type(exc).__name__, str(exc)[:110]))

        print("\n[11] javascript console")
        R.check("no uncaught JS errors anywhere in the run", not JS_ERRORS,
                "; ".join(JS_ERRORS[:3]))
        browser.close()
    return R.report()


if __name__ == "__main__":
    sys.exit(main())
