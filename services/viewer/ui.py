"""Shared visual language for the manager, dashboards, and both map viewers."""
import html


def e(value):
    return html.escape(str(value if value is not None else "—"), quote=True)


CSS = """
:root{--ink:#17354a;--muted:#526778;--accent:#1f78b4;--line:#d8e2e9;--surface:#fff;--canvas:#f4f7f9;--radius:6px;
--font:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
body{margin:0;color:var(--ink);font:14px/1.5 var(--font);background:var(--canvas)}
.app-page *,.app-header *,.map-toolbar *{box-sizing:border-box}
.app-page a,.app-header a,.map-toolbar a{color:#156598;text-underline-offset:3px}
.app-page a:hover,.app-header a:hover,.map-toolbar a:hover{color:#10496e}
:focus-visible{outline:3px solid #bd7109;outline-offset:3px}
.skip-link{position:fixed;left:-9999px;top:8px;z-index:40000;background:white;padding:10px 16px}
.skip-link:focus{left:8px}
.app-header{flex:none;background:var(--surface);border-bottom:1px solid var(--line);position:relative;z-index:20000}
.app-bar{max-width:1232px;min-height:56px;margin:auto;padding:10px 24px;display:flex;gap:24px;align-items:center;flex-wrap:wrap}
.app-header .brand{font-size:17px;font-weight:700;color:var(--ink);text-decoration:none;white-space:nowrap}
.app-nav{display:flex;align-items:center;gap:6px;flex:1;flex-wrap:wrap}
.app-header .app-nav a{padding:6px 10px;text-decoration:none;border-radius:4px;color:var(--muted)}
.app-header .app-nav a[aria-current=page]{color:#145d8c;background:#e9f2f8;font-weight:600}
.app-header .app-nav a:hover{background:var(--canvas);color:var(--ink)}
.account{display:flex;align-items:center;gap:12px;margin-left:auto;min-width:0}
.identity{color:var(--muted);font-size:12px;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.app-header form{margin:0}
.app-page button,.app-header button,.map-toolbar a,.app-page .button{
font:inherit;line-height:1.4;border:1px solid #afbdc7;background:white;color:var(--ink);border-radius:4px;padding:7px 12px;cursor:pointer;text-decoration:none;display:inline-block}
.app-page button:hover,.app-header button:hover,.app-page .button:hover{background:#e9f2f8;border-color:#7e9bad}
.app-page .primary{background:#1b6eaa;border-color:#1b6eaa;color:white}
.app-page .primary:hover{background:#155984;color:white}
.app-page .danger{color:#9d2331;border-color:#d5a7ad}
.app-page .danger:hover{background:#fcecef}
.app-main{max-width:1232px;margin:auto;padding:28px 24px 48px}
.app-page h1{font-size:26px;line-height:1.25;margin:0;font-weight:650;overflow-wrap:anywhere}
.app-page h2{font-size:17px;line-height:1.4;margin:0;font-weight:650}
.app-page h3{font-size:14px;margin:16px 0 6px}
.app-page p{max-width:76ch;margin:8px 0 16px}
.page-heading,.section-title{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}
.page-heading{margin-bottom:20px}.page-heading h1 .badge{vertical-align:middle}
.section-title{margin-bottom:14px}
.caption,.muted{color:var(--muted);font-size:12px}
.count{color:var(--muted);font-size:13px;font-weight:400;margin-left:6px}
.app-page section{margin:20px 0}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:20px}
.metrics{display:flex;gap:28px;flex-wrap:wrap;margin:20px 0 24px}
.metrics div{display:flex;align-items:baseline;gap:8px}.metrics dt{color:var(--muted);order:2;font-size:13px}
.metrics dd{font-size:22px;font-weight:600;margin:0;font-variant-numeric:tabular-nums}
.grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:20px}.grid>section{margin:0}
.table-wrap{overflow:auto}
.app-page table{width:100%;border-collapse:collapse;text-align:left}
.app-page th{color:var(--muted);font-size:12px;font-weight:600;white-space:nowrap}
.app-page th,.app-page td{padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:top}
.app-page th:first-child,.app-page td:first-child{padding-left:0}.app-page td small{display:block;color:var(--muted);font-size:12px}
.app-page tbody tr:last-child td{border-bottom:0}.app-page td{font-variant-numeric:tabular-nums}
.badge{display:inline-block;font-size:11px;line-height:1.5;font-weight:600;border-radius:3px;padding:2px 6px;background:#e9eff3;white-space:nowrap}
.badge.success,.badge.done{color:#176246;background:#e8f3ed}.badge.error{color:#9d2331;background:#fcecef}
.badge.incomplete,.badge.queued,.badge.running{color:#75520b;background:#fff3d3}
.badge.active{color:#145d8c;background:#e5f1f9}
.app-page input,.app-page select{box-sizing:border-box;font:inherit;line-height:1.4;padding:7px 10px;border:1px solid #afbdc7;border-radius:4px;background:white;color:var(--ink);max-width:100%}
.app-page label{display:block;font-size:12px;font-weight:600;margin-bottom:5px}
.filters{display:flex;gap:12px;flex-wrap:wrap;align-items:end;margin:12px 0 16px}
.audit{border-top:1px solid var(--line);padding:0}
.audit>summary{padding:12px 0;cursor:pointer;display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;list-style:none}
.audit>summary::-webkit-details-marker{display:none}
.audit>summary::before{content:"+";color:var(--accent);font-weight:700;width:10px;flex:none}
.audit[open]>summary::before{content:"−"}
.audit time{font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums}
.audit .action{font-weight:600;overflow-wrap:anywhere}
.audit .duration{font-size:12px;color:var(--muted);margin-left:auto}
.audit .detail{padding:0 0 16px 20px}
.detail-grid{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:6px 16px;margin:0 0 14px;font-size:12px}
.detail-grid dt{color:var(--muted)}.detail-grid dd{margin:0;overflow-wrap:anywhere}
.app-page pre{margin:8px 0 12px;padding:12px;background:var(--canvas);border:1px solid var(--line);border-radius:4px;white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.6 ui-monospace,monospace}
.app-page code{overflow-wrap:anywhere}
.pagination{display:flex;gap:16px;align-items:center;font-size:13px;margin:16px 0 0}
.empty{color:var(--muted);padding:12px 0}
.inventory{list-style:none;margin:0;padding:0}.inventory li{padding:9px 0;border-top:1px solid var(--line)}
.inventory li:first-child{border:0}.inventory small{display:block;color:var(--muted)}
.subnav{display:flex;gap:18px;overflow:auto;padding:0 0 12px;border-bottom:1px solid var(--line);margin:18px 0 22px}
.subnav a{white-space:nowrap;font-size:13px}
.metadata>summary{cursor:pointer;font-size:12px;color:var(--muted)}
.metadata .detail-grid{margin-top:12px}
.workspace-row{border-top:1px solid var(--line);padding:18px 0}
.workspace-row:first-of-type{border-top:0;padding-top:0}.workspace-row:last-child{padding-bottom:0}
.workspace-row .section-title{margin-bottom:8px}
.workspace-actions{display:flex;gap:10px 16px;align-items:baseline;flex-wrap:wrap}
.workspace-actions form{margin:0}.workspace-actions summary{cursor:pointer;color:#156598;font-size:13px}
.workspace-actions .danger-summary{color:#9d2331}
.workspace-actions details[open]{flex-basis:100%}
.workspace-actions .action-form{display:flex;gap:10px;align-items:end;flex-wrap:wrap;margin-top:12px}
.workspace-actions .action-form input{width:240px}
.login{max-width:380px;margin:8vh auto}.login h1{margin-bottom:24px}
.login input{width:100%}.login button{width:100%;margin-top:16px}
.alert{padding:10px 12px;border-radius:4px;background:#fcecef;color:#9d2331}
.app-page section[id]{scroll-margin-top:16px}
.services-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}
.services-grid>section{margin:0}.service .detail-grid{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}
.service-action{border-top:1px solid var(--line);margin-top:16px;padding-top:12px}
.service-action summary{cursor:pointer;color:#156598}
.service-action p{margin:8px 0}.notice{padding:10px 12px;background:#e8f3ed;color:#176246;border-radius:4px}
@media(max-width:1000px){.services-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:680px){.services-grid{grid-template-columns:minmax(0,1fr)}}
.map-page{display:flex;flex-direction:column;height:100vh;height:100dvh;overflow:hidden}
.map-page .app-bar{max-width:none}
.map-toolbar{display:flex;align-items:center;gap:16px;justify-content:space-between;padding:8px 16px;background:white;border-bottom:1px solid var(--line);flex:none}
.map-toolbar h1{font:600 15px/1.35 var(--font);margin:0;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.renderer-nav{display:flex;gap:0;flex:none}
.map-toolbar .renderer-nav a{padding:5px 10px;font-size:12px;border-radius:0;margin-left:-1px}
.map-toolbar .renderer-nav a:first-child{border-radius:4px 0 0 4px;margin-left:0}
.map-toolbar .renderer-nav a:last-child{border-radius:0 4px 4px 0}
.map-toolbar .renderer-nav a[aria-current=page]{color:#145d8c;background:#e5f1f9;font-weight:600}
.map-stage{position:relative;flex:1;min-height:0}
@media(max-width:680px){
.app-bar{padding:10px 16px;gap:8px 16px}.app-nav{gap:2px;order:3;flex-basis:100%;flex-wrap:wrap}
.app-header .app-nav a{padding:5px 8px;white-space:nowrap}.identity{display:none}.account{margin-left:auto}
.app-main{padding:22px 16px 36px}.app-page h1{font-size:23px}.panel{padding:16px}
.metrics{gap:12px 22px}.metrics dd{font-size:20px}
.grid{grid-template-columns:minmax(0,1fr)}.app-page th,.app-page td{padding:9px 10px}
.audit .duration{margin-left:0}.audit .action{max-width:100%}
.audit .detail{padding-left:0}.detail-grid{gap:5px 10px}
.map-toolbar{padding:8px 12px;gap:8px}.map-toolbar h1{font-size:14px}
.map-page .app-nav{order:initial;flex-basis:auto;flex:none;margin-left:auto}
.map-page .account{display:none}.map-page .app-nav a:not(:first-child){display:none}
}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
"""


def header(principal=None, csrf="", active=""):
    links = [("/dashboard", "Översikt", "dashboard"), ("/workspaces", "Arbetsytor", "workspaces")]
    if principal and principal.get("is_admin"):
        links.extend((("/admin", "Administration", "admin"), ("/admin/services", "Tjänster", "services")))
    if principal:
        links.append(("/architecture", "Arkitektur", "architecture"))
    nav = "".join(f'<a href="{path}"' + (' aria-current="page"' if active == key else '') +
                  f'>{label}</a>' for path, label, key in links)
    if principal:
        account = (f'<span class="identity" title="{e(principal.get("name") or principal["id"])}">'
                   f'{e(principal.get("name") or principal["id"])}</span>'
                   f'<form method="post" action="/logout"><input type="hidden" name="csrf" value="{e(csrf)}">'
                   '<button type="submit">Logga ut</button></form>')
    else:
        account = '' if active == "login" else '<a href="/login">Logga in</a>'
    navigation = (f'<nav class="app-nav" aria-label="Huvudnavigation">{nav}</nav>'
                  if active != "login" else "")
    return ('<header class="app-header"><div class="app-bar">'
            '<a class="brand" href="/dashboard">Geodata MCP</a>'
            f'{navigation}<div class="account">{account}</div></div></header>')



def document(title, body, principal=None, csrf="", active=""):
    return f"""<!doctype html><html lang="sv"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)} – Geodata MCP</title>
<style>{CSS}</style></head><body class="app-page"><a class="skip-link" href="#main">Hoppa till innehåll</a>
{header(principal, csrf, active)}<main class="app-main" id="main">{body}</main></body></html>"""


def map_header(view_id, renderer, title, principal=None, csrf=""):
    links = "".join(
        f'<a href="/v/{e(view_id)}{suffix}"' +
        (' aria-current="page"' if renderer == key else '') + f'>{label}</a>'
        for suffix, key, label in (("", "maplibre", "MapLibre"), ("?renderer=origo", "origo", "Origo")))
    return (header(principal, csrf) +
            f'<div class="map-toolbar"><h1 id="titlebar">{e(title or "Karta")}</h1>'
            f'<nav class="renderer-nav" aria-label="Kartvisare">{links}</nav></div>')
