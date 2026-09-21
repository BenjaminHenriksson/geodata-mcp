"""Server-rendered dashboards; every stored value is escaped, with no inline scripts."""
import html
from datetime import timezone
from urllib.parse import urlencode

CSS = """
:root{--ink:#17354a;--muted:#526778;--blue:#1f78b4;--line:#d8e2e9;--pale:#edf5fa;--paper:#fff}
*{box-sizing:border-box}body{margin:0;background:#f5f8fa;color:var(--ink);font:15px/1.6 system-ui,sans-serif}
a{color:#156598;text-underline-offset:3px}a:hover{color:#10496e}
:focus-visible{outline:3px solid #d77b12;outline-offset:3px}
header{background:#17354a;color:white;padding:18px max(24px,calc((100vw - 1180px)/2))}
header .bar{display:flex;justify-content:space-between;align-items:center;gap:20px;flex-wrap:wrap}
.brand{font-size:20px;font-weight:700;color:white;text-decoration:none}
nav{display:flex;gap:22px;align-items:center;flex-wrap:wrap}nav a{color:white}
nav a[aria-current=page]{text-decoration-thickness:3px}
header button{background:transparent;color:white;border-color:#8ba4b5}
main{max-width:1228px;margin:auto;padding:32px 24px 60px}
h1{font-size:32px;line-height:1.2;margin:12px 0}h2{font-size:21px;margin:0 0 12px}
h3{font-size:17px;margin:0 0 8px}p{max-width:76ch;margin:8px 0 20px}
.muted{color:var(--muted)}.lead{font-size:17px}
section{margin:28px 0}.panel{background:white;border:1px solid var(--line);padding:24px;border-radius:8px}
.stats{display:flex;flex-wrap:wrap;background:var(--pale);border-left:4px solid var(--blue);margin:24px 0;padding:18px 0}
.stat{padding:0 24px;min-width:140px}.stat strong{display:block;font-size:28px;line-height:1.3;font-variant-numeric:tabular-nums}
.stat span{color:var(--muted);font-size:14px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:24px}
.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;text-align:left}
th{color:var(--muted);font-size:13px;font-weight:600;white-space:nowrap}
th,td{padding:13px 12px;border-bottom:1px solid var(--line);vertical-align:top}
th:first-child,td:first-child{padding-left:0}td small{display:block;color:var(--muted)}
td.numeric{font-variant-numeric:tabular-nums}tbody tr:last-child td{border-bottom:0}
.badge{display:inline-block;font-size:12px;font-weight:650;border-radius:4px;padding:2px 8px;background:#e7eff4;white-space:nowrap}
.success,.done{color:#166446;background:#e7f4ed}.error{color:#9d2331;background:#fcecef}
.incomplete,.queued,.running{color:#74520c;background:#fff3d3}.active{color:#155a88;background:#e2f2fc}
button,select{font:inherit;border:1px solid #9aadb9;border-radius:4px;background:white;padding:7px 12px}
button{cursor:pointer}button:hover{background:var(--pale);color:var(--ink)}
.filters{display:flex;gap:16px;flex-wrap:wrap;align-items:end;margin:18px 0}
label{display:block;font-size:13px;font-weight:600;margin-bottom:4px}
.audit{padding:16px 0;border-top:1px solid var(--line)}.audit summary{cursor:pointer;display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
.audit summary::before{content:'+';font-weight:700;color:var(--blue)}.audit[open] summary::before{content:'−'}
.audit time{color:var(--muted);font-size:13px}.audit .action{font-weight:650;overflow-wrap:anywhere}
.audit .detail{padding:16px 0 8px}.detail p{margin:4px 0 10px;font-size:14px}
pre{padding:16px;background:#f0f5f8;border-left:3px solid #89b9d8;white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.6 ui-monospace,monospace}
code{overflow-wrap:anywhere}.pagination{display:flex;gap:20px;align-items:center;margin:18px 0}
.empty{padding:18px;background:#f3f7fa;color:var(--muted)}.section-title{display:flex;justify-content:space-between;gap:16px;align-items:baseline;flex-wrap:wrap}
ul.inventory{list-style:none;padding:0;margin:0}.inventory li{padding:10px 0;border-bottom:1px solid var(--line)}
.inventory li:last-child{border:0}.inventory small{display:block;color:var(--muted)}
.skip{position:absolute;left:-9999px}.skip:focus{left:12px;top:8px;background:white;padding:12px;z-index:1}
footer{font-size:13px;color:var(--muted);border-top:1px solid var(--line);padding-top:18px;margin-top:32px}
@media(max-width:680px){main{padding:22px 16px}.panel{padding:16px}.grid{grid-template-columns:1fr}
h1{font-size:27px}header{padding:16px}nav{gap:14px}.stat{min-width:50%;padding:10px 16px}th,td{padding:10px 8px}}
"""


def e(value):
    return html.escape(str(value if value is not None else "—"), quote=True)


def stamp(value):
    if value is None:
        return "—"
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if hasattr(value, "strftime") else str(value)


def badge(status):
    labels = {"success": "Lyckades", "error": "Fel", "incomplete": "Utan slutpost",
              "queued": "Kö", "running": "Pågår", "done": "Klart", "cancelled": "Avbrutet",
              "active": "Aktiv"}
    return f'<span class="badge {e(status)}">{e(labels.get(status, status))}</span>'


def shell(title, body, principal, csrf, active=""):
    admin = '<a href="/admin"' + (' aria-current="page"' if active == "admin" else '') + '>Administration</a>' if principal["is_admin"] else ""
    return f"""<!doctype html><html lang="sv"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)} – Geodata MCP</title>
<style>{CSS}</style></head><body><a class="skip" href="#main">Hoppa till innehåll</a>
<header><div class="bar"><a class="brand" href="/dashboard">Geodata MCP</a>
<nav aria-label="Huvudnavigation"><a href="/dashboard" {'aria-current="page"' if active == 'dashboard' else ''}>Min översikt</a>
<a href="/workspaces">Hantera arbetsytor</a>{admin}
<form action="/logout" method="post"><input type="hidden" name="csrf" value="{e(csrf)}"><button>Logga ut</button></form></nav></div></header>
<main id="main">{body}<footer>Inloggad som {e(principal['name'] or principal['id'])}. Alla tider visas i UTC.</footer></main></body></html>"""


def stats(items):
    return '<div class="stats">' + "".join(
        f'<div class="stat"><strong>{e(value)}</strong><span>{e(label)}</span></div>'
        for label, value in items) + '</div>'


def table(headers, rows):
    return ('<div class="table-wrap"><table><thead><tr>' +
            "".join(f'<th scope="col">{e(h)}</th>' for h in headers) +
            '</tr></thead><tbody>' + "".join('<tr>' + "".join(f'<td>{cell}</td>' for cell in row) + '</tr>' for row in rows) +
            '</tbody></table></div>')


def pagination(path, current, more, params=None, anchor=""):
    params = params or {}
    parts = [f'<span>Sida {current}</span>']
    for label, number in (("Föregående", current - 1), ("Nästa", current + 1)):
        if number > 0 and (number < current or more):
            url = path + "?" + urlencode(dict(params, page=number)) + anchor
            parts.append(f'<a href="{e(url)}">{label}</a>')
    return '<nav class="pagination" aria-label="Sidindelning">' + "".join(parts) + '</nav>'


def overview_page(data, principal, csrf, *, admin=False, current=1):
    title = "Administration" if admin else "Dina arbetsytor"
    intro = ("Överblick över användare, arbetsytor och den gemensamma datakatalogen."
             if admin else "Följ dina analyser, öppna kartor och se vad MCP-klienten har gjort i varje arbetsyta.")
    totals = data["totals"]
    body = f'<h1>{title}</h1><p class="lead">{intro}</p>'
    body += stats([("Arbetsytor", totals["workspaces"]), ("Lager", totals["layers"]),
                   ("Kartor", totals["maps"]), ("Jobb i kö eller pågående", totals["pending"])])
    body += '<section class="panel"><div class="section-title"><h2>Arbetsytor</h2>'
    body += '<a href="/admin/audit">Granska all SQL- och MCP-logg</a>' if admin else '<a href="/workspaces">Hantera arbetsytor</a>'
    body += '</div><p class="muted">Öppna en arbetsyta för lager, kartor, jobb och revisionslogg. Aktiv arbetsyta tar emot nästa MCP-anrop.</p>'
    rows = []
    for w in data["workspaces"]:
        name = f'<a href="/workspaces/{e(w["id"])}"><strong>{e(w["name"])}</strong></a>'
        name += " " + badge("active") if w["is_active"] else ""
        if admin:
            name += f'<small>{e(w["owner"])}{" (inaktiverad)" if w["disabled"] else ""}</small>'
        rows.append([name, e(w["layers"]), e(w["maps"]), e(w["pending"]),
                     f'{e(w["calls"])} MCP / {e(w["queries"])} SQL',
                     e(stamp(w["last_used"])),
                     f'<a href="/workspaces/{e(w["id"])}#audit">Visa logg</a>'])
    body += table(["Arbetsyta", "Lager", "Kartor", "Jobb", "Anrop senaste 24 h", "Senast använd", "Revisionslogg"], rows) if rows else '<p class="empty">Inga arbetsytor ännu. Anslut din MCP-klient och kör workspace för att börja.</p>'
    body += pagination("/admin" if admin else "/dashboard", current, data["more"]) + '</section>'
    if admin:
        c = data["catalog"]
        body += '<section class="panel"><h2>Gemensam katalog</h2>'
        body += stats([("Källor", c["sources"]), ("Dataset", c["datasets"]),
                       ("Dokument", c["documents"]), ("Aktiva användare / nycklar", c["users"])]) + '</section>'
        body += '<section class="panel"><h2>Användare och behörigheter</h2><p class="muted">En användare motsvarar en API-nyckel eller en OAuth-identitet. Administratörsbehörighet tilldelas av driftansvarig.</p>'
        body += table(["Användare", "Roll", "Status", "Arbetsytor", "Senast använd"], [
            [e(u["name"] or u["id"]), "Administratör" if u["is_admin"] else "Användare",
             "Inaktiverad" if u["disabled"] else "Aktiv", e(u["workspaces"]), e(stamp(u["last_used"]))]
            for u in data["users"]])
        body += pagination("/admin", current, data["users_more"]) + '</section>'
    return shell(title, body, principal, csrf, "admin" if admin else "dashboard")


def audit_section(data, path, kind, status, current, *, all_workspaces=False):
    body = '<section class="panel" id="audit"><h2>SQL- och MCP-logg</h2>'
    body += '<p class="muted">MCP visar verktygsanrop. SQL visar läsfrågor och SQL skickad till lagerverktyget, även avvisade försök. Öppna en post för detaljer och spårnings-ID.</p>'
    body += f'<form class="filters" method="get" action="{e(path)}#audit">'
    for name, label, selected, choices in [
        ("kind", "Typ", kind, [("", "Alla"), ("mcp", "MCP"), ("sql", "SQL")]),
        ("status", "Resultat", status, [("", "Alla"), ("success", "Lyckades"), ("error", "Fel"), ("incomplete", "Utan slutpost")]),
    ]:
        body += f'<div><label for="{name}">{label}</label><select id="{name}" name="{name}">'
        body += "".join(f'<option value="{value}"{" selected" if selected == value else ""}>{text}</option>' for value, text in choices)
        body += '</select></div>'
    body += '<button type="submit">Filtrera logg</button></form>'
    if not data["events"]:
        body += '<p class="empty">Inga loggposter matchar urvalet. SQL-historik visas när frågor körs och MCP-anrop loggas från att auditfunktionen aktiveras.</p>'
    for row in data["events"]:
        duration = f'{row["duration_ms"]} ms' if row["duration_ms"] is not None else "Utan slutpost"
        body += f'<details class="audit"><summary><time>{e(stamp(row["ts"]))}</time><span class="badge">{e(row["kind"].upper())}</span><span class="action">{e(row["action"])}</span>{badge(row["status"])}<span class="muted">{e(duration)}</span></summary><div class="detail">'
        if all_workspaces:
            body += f'<p>Arbetsyta: {e(row["workspace_name"])} <code>{e(row["workspace_id"])}</code></p>'
        body += f'<p>Utfört av: {e(row["actor"] or row["api_key_id"] or "Äldre post, identitet saknas")}</p>'
        for label, field in [("Logg-ID", "id"), ("MCP-anrop", "call_id"), ("Fråge-ID", "query_id"), ("Jobb", "job_id"), ("Resultatrader", "row_count")]:
            if row[field] is not None:
                body += f'<p>{label}: <code>{e(row[field])}</code></p>'
        if row["references"]:
            label = "Argumentnamn" if row["kind"] == "mcp" else "Refererade tabeller"
            body += f'<p>{label}: {e(", ".join(row["references"]))}</p>'
        if row["sql_text"]:
            body += f'<h3>SQL</h3><pre>{e(row["sql_text"])}</pre>'
        if row["error"]:
            body += f'<h3>Fel</h3><pre>{e(row["error"])}</pre>'
        if row["status"] == "incomplete":
            body += '<p>Anropet pågår eller saknar slutpost efter ett avbrott. Kontrollera arbetsytans tillstånd innan du försöker igen.</p>'
        body += '</div></details>'
    body += pagination(path, current, data["more"], {"kind": kind, "status": status}, "#audit")
    body += '<p class="muted">SQL-text sparas i loggen. Undvik att skriva hemligheter i SQL. Resultatdata och råa autentiseringsuppgifter kopieras inte till MCP-loggen.</p></section>'
    return body


def workspace_page(w, audit, principal, csrf, *, kind="", status="", current=1):
    body = f'<a href="{"/admin" if principal["is_admin"] else "/dashboard"}">Till översikten</a>'
    body += f'<h1>{e(w["name"])} {" " + badge("active") if w["is_active"] else ""}</h1>'
    body += f'<p class="muted">Ägare: {e(w["owner"])}. Skapad {e(stamp(w["created_at"]))}.<br>Schema: <code>{e(w["ws_schema"])}</code></p>'
    body += '<p><a href="#audit">Granska SQL- och MCP-logg</a> &nbsp; <a href="/workspaces">Hantera arbetsytor</a></p>'
    body += stats([("MCP-anrop senaste 24 h", w["activity"]["calls"]),
                   ("Anrop med fel senaste 24 h", w["activity"]["errors"]),
                   ("Utan slutpost senaste 24 h", w["activity"]["incomplete"])])
    body += '<div class="grid"><section class="panel"><h2>Lager</h2><ul class="inventory">'
    body += "".join(f'<li><code>{e(layer["name"])}</code><small>{e(layer["label"] or layer["notes"] or "")}</small></li>' for layer in w["layers"]) or '<li class="empty">Inga lager ännu. Skapa ett lager med MCP-verktyget layer.</li>'
    body += '</ul><p class="muted">Visar upp till 200 lager.</p></section><section class="panel"><h2>Kartor</h2><ul class="inventory">'
    body += "".join(f'<li><a href="/v/{e(m["view_id"])}">{e(m["title"] or "Namnlös karta")}</a><small>Uppdaterad {e(stamp(m["updated_at"]))}</small></li>' for m in w["maps"]) or '<li class="empty">Inga kartor ännu. Använd MCP-verktyget map för att visa dina lager.</li>'
    body += '</ul><p class="muted">Visar upp till 100 kartor.</p></section></div>'
    body += '<section class="panel"><h2>Senaste jobb</h2><p class="muted">De senaste 25 jobben för den här arbetsytan. Ladda om sidan för aktuell status.</p>'
    body += table(["Jobb", "Typ", "Status", "Försök", "Skapat"], [
        [e(j["id"]), e(j["kind"]), badge(j["status"]), e(j["attempts"]), e(stamp(j["created_at"]))]
        for j in w["jobs"]]) if w["jobs"] else '<p class="empty">Inga jobb har startats i arbetsytan.</p>'
    body += '</section>'
    body += audit_section(audit, f'/workspaces/{w["id"]}', kind, status, current)
    body += '<section class="panel"><h2>Ändringshistorik</h2><p class="muted">De senaste 25 proveniensposterna: ändringar av data och lager, inklusive databasens DDL-händelser.</p>'
    for p in w["provenance"]:
        body += f'<details class="audit"><summary><time>{e(stamp(p["ts"]))}</time><span>{e(p["kind"])}</span><code>{e(p["object_ref"])}</code></summary>'
        body += f'<p>Proveniens-ID: {e(p["id"])}. Jobb: {e(p["job_id"])}.</p>'
        if p["sql_text"]:
            body += f'<pre>{e(p["sql_text"])}</pre>'
        body += '</details>'
    if not w["provenance"]:
        body += '<p class="empty">Inga registrerade dataändringar ännu.</p>'
    body += '</section>'
    return shell(w["name"], body, principal, csrf)


def admin_audit_page(data, principal, csrf, *, kind="", status="", current=1):
    body = '<a href="/admin">Till administrationen</a><h1>Revisionslogg för alla arbetsytor</h1><p>Här finns även historik för borttagna arbetsytor.</p>'
    body += audit_section(data, "/admin/audit", kind, status, current, all_workspaces=True)
    return shell("Revisionslogg", body, principal, csrf, "admin")
