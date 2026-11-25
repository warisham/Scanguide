"""Rendering and resolution tests for ScanGuide. Usage: python3 -I test_scanguide.py <index.html> <outdir>"""
import sys, json, os
from playwright.sync_api import sync_playwright

PAGE = os.path.abspath(sys.argv[1]); OUT = sys.argv[2]; os.makedirs(OUT, exist_ok=True)
AXE = open(os.environ.get("AXE_PATH", "node_modules/axe-core/axe.min.js")).read()
VIEWPORTS = [("phone-small", 320, 640), ("phone", 390, 844), ("tablet", 768, 1024),
             ("laptop", 1024, 768), ("desktop", 1366, 768), ("wide", 1920, 1080)]
failures = []
def check(cond, msg):
    if not cond: failures.append(msg)
    return cond

LAYOUT_JS = r"""
() => {
  const vw = document.documentElement.clientWidth;
  const out = {scrollW: document.documentElement.scrollWidth, vw, overflow: [], smallText: [], smallTargets: [], badUse: [], lowContrast: []};
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && !e.closest('.sr-only') && !e.closest('details:not([open]) > :not(summary)'); };
  for (const e of document.querySelectorAll('body *')) {
    if (!vis(e) || e.closest('svg') && e.tagName.toLowerCase() !== 'svg') continue;
    const r = e.getBoundingClientRect();
    if (r.right > vw + 1 || r.left < -1) out.overflow.push((e.id || e.className || e.tagName).toString().slice(0,60) + ` [${Math.round(r.left)}..${Math.round(r.right)}]`);
    const direct = [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    if (direct) {
      const fs = parseFloat(getComputedStyle(e).fontSize);
      if (fs < 16) out.smallText.push(`${e.tagName}.${e.className} ${fs}px "${e.textContent.trim().slice(0,30)}"`);
    }
  }
  for (const e of document.querySelectorAll('button, a.btn, input:not([type=checkbox]), textarea, summary, .asks label')) {
    if (!vis(e)) continue; const r = e.getBoundingClientRect();
    if (r.height < 44) out.smallTargets.push(`${e.tagName}#${e.id} ${Math.round(r.width)}x${Math.round(r.height)}`);
  }
  for (const u of document.querySelectorAll('use')) {
    const id = (u.getAttribute('href') || '').slice(1); if (!document.getElementById(id)) out.badUse.push(id);
  }
  return out;
}"""

with sync_playwright() as p:
    browser = p.chromium.launch()
    for name, w, h in VIEWPORTS:
        ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=2 if w < 800 else 1)
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append("console: " + m.text))
        # Google Fonts can't be reached from this sandbox; block them so the test sees the fallback stack.
        page.route("**/fonts.g*/**", lambda r: r.abort())
        page.goto("file://" + PAGE); page.wait_for_timeout(400)
        errors = [e for e in errors if "ERR_FAILED" not in e and "fonts.g" not in e]
        check(not errors, f"{name}: JS/console errors {errors}")

        L = page.evaluate(LAYOUT_JS)
        check(L["scrollW"] <= L["vw"], f"{name}: horizontal scroll ({L['scrollW']} > {L['vw']})")
        check(not L["overflow"], f"{name}: elements outside viewport {L['overflow'][:6]}")
        check(not L["smallText"], f"{name}: text under 16px {L['smallText'][:6]}")
        check(not L["smallTargets"], f"{name}: tap targets under 44px {L['smallTargets'][:6]}")
        check(not L["badUse"], f"{name}: missing SVG symbols {L['badUse']}")

        # Sample report renders as expected
        check(page.locator(".finding").count() == 4, f"{name}: expected 4 findings, got {page.locator('.finding').count()}")
        tally = page.locator(".tally").inner_text()
        check("2 findings to follow up" in tally and "2 routine" in tally, f"{name}: tally wrong: {tally!r}")
        titles = page.locator(".finding h3").all_inner_texts()
        check(titles == ["Lung nodule", "Coronary artery calcium", "Emphysema", "Hiatal hernia"], f"{name}: titles {titles}")
        check("Book a repeat scan in 6–12 months" in page.locator(".finding").first.inner_text().replace("\u2060",""), f"{name}: nodule next step missing")
        organs = [u.get_attribute("href") for u in page.locator(".finding svg.organ use").all()]
        check(organs == ["#i-lungs", "#i-heart", "#i-lungs", "#i-stomach"], f"{name}: organ pictures {organs}")
        check(page.locator(".asks li").count() == 5, f"{name}: questions count {page.locator('.asks li').count()}")
        # every picture actually paints (non-zero box)
        for sel in [".hero figure svg", ".steps svg", ".finding svg.organ", ".remind svg.cal", ".assure svg"]:
            for i, s in enumerate(page.locator(sel).all()):
                b = s.bounding_box(); check(b and b["width"] > 20 and b["height"] > 20, f"{name}: picture {sel}[{i}] not painted {b}")

        page.screenshot(path=f"{OUT}/{name}.png", full_page=True)

        if name in ("phone", "desktop"):
            page.locator(".finding summary").first.click()
            check(page.locator(".finding details").first.get_attribute("open") is not None, f"{name}: details did not open")
            page.locator(".asks input").first.check()
            page.fill("#remindEmail", "bad"); page.click("#remindBtn")
            check(page.locator("#remindOk").is_hidden(), f"{name}: reminder accepted bad email")
            page.fill("#remindEmail", "pat@example.com"); page.click("#remindBtn")
            check(page.locator("#remindOk").is_visible(), f"{name}: reminder confirmation missing")
            page.screenshot(path=f"{OUT}/{name}-interacted.png", full_page=True)
            page.click("#clear")
            check(page.locator(".finding").count() == 0 and page.locator(".asks li").count() == 3, f"{name}: clear did not reset")
            page.fill("#report", "IMPRESSION: Lung-RADS 2. No suspicious pulmonary nodules. Small hiatal hernia. Mild coronary artery calcification.")
            page.click("#explain"); page.wait_for_timeout(200)
            t = page.locator(".finding h3").all_inner_texts()
            check("Lung-RADS 2" in t and "Hiatal hernia" in t and "Coronary artery calcium" in t, f"{name}: custom report findings {t}")
            page.fill("#report", "nothing relevant here"); page.click("#explain")
            check(page.locator(".finding").count() == 0 and "did not find" in page.locator("#results").inner_text(), f"{name}: empty-state broken")
            page.click("#loadSample")
            check(page.locator(".finding").count() == 4, f"{name}: sample reload broken")
            page.locator("#authArea .gsi").click()
            check(page.locator("#acct").is_visible(), f"{name}: sign-in did not show account")
            check(not errors, f"{name}: errors after interaction {errors}")

        if name == "desktop":
            page.add_script_tag(content=AXE)
            res = page.evaluate("async () => (await axe.run(document, {runOnly:['wcag2a','wcag2aa','wcag21aa','best-practice']})).violations.map(v => ({id:v.id, impact:v.impact, n:v.nodes.length, t:v.nodes.slice(0,3).map(n=>n.target.join(' '))}))")
            serious = [v for v in res if v["impact"] in ("serious", "critical")]
            check(not serious, f"axe serious/critical: {serious}")
            json.dump(res, open(f"{OUT}/axe.json", "w"), indent=1)
            print("axe (all):", [(v["id"], v["impact"], v["n"]) for v in res])
        ctx.close()
    browser.close()

print("FAILURES:" if failures else "ALL TESTS PASSED")
for f in failures: print(" -", f)
sys.exit(1 if failures else 0)
