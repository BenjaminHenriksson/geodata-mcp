"""Compact server-rendered workspace and audit views."""
from datetime import timezone
from urllib.parse import urlencode

import ui

e = ui.e


def stamp(value):
    if value is None:
        return "—"
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if hasattr(value, "strftime") else str(value)


def badge(status):
    labels = {"success": "Lyckades", "error": "Fel", "incomplete": "Utan slutpost",
              "queued": "Kö", "running": "Pågår", "done": "Klart", "cancelled": "Avbrutet",
              "active": "Aktiv"}
    title = ' title="Används för nästa MCP-anrop"' if status == "active" else ""
    return f'<span class="badge {e(status)}"{title}>{e(labels.get(status, status))}</span>'


def shell(title, body, principal, csrf, active=""):
    return ui.document(title, body, principal, csrf, active)


def stats(items):
    return '<dl class="metrics">' + "".join(
        f'<div><dt>{e(label)}</dt><dd>{e(value)}</dd></div>' for label, value in items) + '</dl>'


def table(headers, rows):
    return ('<div class="table-wrap"><table><thead><tr>' +
            "".join(f'<th scope="col">{e(h)}</th>' for h in headers) +
            '</tr></thead><tbody>' + "".join('<tr>' + "".join(f'<td>{cell}</td>' for cell in row) + '</tr>' for row in rows) +
            '</tbody></table></div>')


def pagination(path, current, more, params=None, anchor=""):
    if current == 1 and not more:
        return ""
    params = params or {}
    parts = [f'<span>Sida {current}</span>']
    for label, number in (("Föregående", current - 1), ("Nästa", current + 1)):
        if number > 0 and (number < current or more):
            url = path + "?" + urlencode(dict(params, page=number)) + anchor
            parts.append(f'<a href="{e(url)}">{label}</a>')
    return '<nav class="pagination" aria-label="Sidindelning">' + "".join(parts) + '</nav>'


def overview_page(data, principal, csrf, *, admin=False, current=1):
    title = "Administration" if admin else "Översikt"
    totals = data["totals"]
    body = f'<div class="page-heading"><h1>{title}</h1></div>'
    body += stats([("arbetsytor", totals["workspaces"]), ("lager", totals["layers"]),
                   ("kartor", totals["maps"]), ("pågående / köade jobb", totals["pending"])])
    body += '<section class="panel"><div class="section-title"><h2>Arbetsytor</h2>'
    if admin:
        body += '<a href="/admin/audit">SQL- och MCP-logg</a>'
    body += '</div>'
    rows = []
    for w in data["workspaces"]:
        name = f'<a href="/workspaces/{e(w["id"])}"><strong>{e(w["name"])}</strong></a>'
        name += " " + badge("active") if w["is_active"] else ""
        if admin:
            name += f'<small>{e(w["owner"])}{" (inaktiverad)" if w["disabled"] else ""}</small>'
        rows.append([name, e(w["layers"]), e(w["maps"]), e(w["pending"]),
                     f'{e(w["calls"])} MCP / {e(w["queries"])} SQL',
                     e(stamp(w["last_used"])),
                     f'<a href="/workspaces/{e(w["id"])}#audit">Logg</a>'])
    body += table(["Arbetsyta", "Lager", "Kartor", "Jobb", "Anrop (24 h)", "Senast använd (UTC)", ""], rows) if rows else '<p class="empty">Inga arbetsytor. Anslut en MCP-klient för att skapa din första.</p>'
    body += pagination("/admin" if admin else "/dashboard", current, data["more"]) + '</section>'
    if admin:
        c = data["catalog"]
        body += '<section class="panel"><h2>Katalog</h2>'
        body += stats([("källor", c["sources"]), ("dataset", c["datasets"]),
                       ("dokument", c["documents"]), ("aktiva användare", c["users"])]) + '</section>'
        body += '<section class="panel"><div class="section-title"><h2>Användare</h2></div>'
        body += table(["Användare", "Roll", "Status", "Arbetsytor", "Senast använd (UTC)"], [
            [e(u["name"] or u["id"]), "Administratör" if u["is_admin"] else "Användare",
             "Inaktiverad" if u["disabled"] else "Aktiv", e(u["workspaces"]), e(stamp(u["last_used"]))]
            for u in data["users"]])
        body += pagination("/admin", current, data["users_more"]) + '</section>'
    return shell(title, body, principal, csrf, "admin" if admin else "dashboard")


def audit_section(data, path, kind, status, current, *, all_workspaces=False):
    body = '<section class="panel" id="audit"><div class="section-title"><h2>SQL- och MCP-logg</h2><span class="caption">UTC</span></div>'
    body += f'<form class="filters" method="get" action="{e(path)}#audit">'
    for name, label, selected, choices in [
        ("kind", "Typ", kind, [("", "Alla"), ("mcp", "MCP"), ("sql", "SQL")]),
        ("status", "Resultat", status, [("", "Alla"), ("success", "Lyckades"), ("error", "Fel"), ("incomplete", "Utan slutpost")]),
    ]:
        body += f'<div><label for="{name}">{label}</label><select id="{name}" name="{name}">'
        body += "".join(f'<option value="{value}"{" selected" if selected == value else ""}>{text}</option>' for value, text in choices)
        body += '</select></div>'
    body += '<button type="submit">Filtrera</button></form>'
    if not data["events"]:
        body += '<p class="empty">Inga loggposter matchar urvalet.</p>'
    for row in data["events"]:
        duration = f'{row["duration_ms"]} ms' if row["duration_ms"] is not None else ""
        body += f'<details class="audit"><summary><time>{e(stamp(row["ts"]))}</time><span class="badge">{e(row["kind"].upper())}</span><span class="action">{e(row["action"])}</span>{badge(row["status"])}<span class="duration">{e(duration)}</span></summary><div class="detail"><dl class="detail-grid">'
        if all_workspaces:
            body += f'<dt>Arbetsyta</dt><dd>{e(row["workspace_name"])} <code>{e(row["workspace_id"])}</code></dd>'
        body += f'<dt>Användare</dt><dd>{e(row["actor"] or row["api_key_id"] or "Ej registrerad")}</dd>'
        for label, field in [("MCP-anrop", "call_id"), ("Fråge-ID", "query_id"), ("Jobb", "job_id"), ("Rader", "row_count")]:
            if row[field] is not None:
                body += f'<dt>{label}</dt><dd><code>{e(row[field])}</code></dd>'
        if row["references"]:
            label = "Argument" if row["kind"] == "mcp" else "Tabeller"
            body += f'<dt>{label}</dt><dd>{e(", ".join(row["references"]))}</dd>'
        body += '</dl>'
        if row["sql_text"]:
            body += f'<h3>SQL</h3><pre>{e(row["sql_text"])}</pre>'
        if row["error"]:
            body += f'<h3>Fel</h3><pre>{e(row["error"])}</pre>'
        if row["status"] == "incomplete":
            body += '<p>Anropet pågår eller saknar slutpost. Kontrollera arbetsytan före nytt försök.</p>'
        body += '</div></details>'
    body += pagination(path, current, data["more"], {"kind": kind, "status": status}, "#audit")
    return body + '</section>'


def limit_note(items, limit):
    return f'<p class="caption">Visar högst {limit} poster.</p>' if len(items) == limit else ""


def workspace_page(w, audit, principal, csrf, *, kind="", status="", current=1):
    body = f'<div class="page-heading"><h1>{e(w["name"])} {" " + badge("active") if w["is_active"] else ""}</h1><a href="/workspaces">Hantera</a></div>'
    body += f'<details class="metadata"><summary>Detaljer</summary><dl class="detail-grid"><dt>Ägare</dt><dd>{e(w["owner"])}</dd><dt>Skapad (UTC)</dt><dd>{e(stamp(w["created_at"]))}</dd><dt>Schema</dt><dd><code>{e(w["ws_schema"])}</code></dd></dl></details>'
    body += '<nav class="subnav" aria-label="Arbetsyta"><a href="#maps">Kartor</a><a href="#layers">Lager</a><a href="#jobs">Jobb</a><a href="#audit">Logg</a><a href="#history">Ändringar</a></nav>'
    body += stats([("MCP-anrop (24 h)", w["activity"]["calls"]),
                   ("fel", w["activity"]["errors"]), ("utan slutpost", w["activity"]["incomplete"])])
    body += '<div class="grid"><section class="panel" id="maps"><div class="section-title"><h2>Kartor</h2></div><ul class="inventory">'
    body += "".join(f'<li><a href="/v/{e(m["view_id"])}">{e(m["title"] or "Namnlös karta")}</a><small>{e(stamp(m["updated_at"]))} UTC</small></li>' for m in w["maps"]) or '<li class="empty">Inga kartor.</li>'
    body += '</ul>' + limit_note(w["maps"], 100) + '</section><section class="panel" id="layers"><div class="section-title"><h2>Lager</h2></div><ul class="inventory">'
    body += "".join(f'<li>{e(layer["label"] or layer["name"])}<small>{e(layer["notes"] or "")}</small></li>' for layer in w["layers"]) or '<li class="empty">Inga lager.</li>'
    body += '</ul>' + limit_note(w["layers"], 200) + '</section></div>'
    body += '<section class="panel" id="jobs"><div class="section-title"><h2>Jobb</h2></div>'
    body += table(["Jobb", "Typ", "Status", "Försök", "Skapat (UTC)"], [
        [e(j["id"]), e(j["kind"]), badge(j["status"]), e(j["attempts"]), e(stamp(j["created_at"]))]
        for j in w["jobs"]]) if w["jobs"] else '<p class="empty">Inga jobb.</p>'
    body += limit_note(w["jobs"], 25) + '</section>'
    body += audit_section(audit, f'/workspaces/{w["id"]}', kind, status, current)
    body += '<section class="panel" id="history"><div class="section-title"><h2>Ändringar</h2><span class="caption">UTC</span></div>'
    for p in w["provenance"]:
        body += f'<details class="audit"><summary><time>{e(stamp(p["ts"]))}</time><span>{e(p["kind"])}</span><code>{e(p["object_ref"])}</code></summary><div class="detail">'
        body += f'<dl class="detail-grid"><dt>Proveniens-ID</dt><dd>{e(p["id"])}</dd>'
        if p["job_id"] is not None:
            body += f'<dt>Jobb</dt><dd>{e(p["job_id"])}</dd>'
        body += '</dl>'
        if p["sql_text"]:
            body += f'<pre>{e(p["sql_text"])}</pre>'
        body += '</div></details>'
    if not w["provenance"]:
        body += '<p class="empty">Inga ändringar.</p>'
    body += limit_note(w["provenance"], 25) + '</section>'
    return shell(w["name"], body, principal, csrf, "dashboard")


def admin_audit_page(data, principal, csrf, *, kind="", status="", current=1):
    body = '<div class="page-heading"><h1>Revisionslogg</h1><a href="/admin">Administration</a></div>'
    body += audit_section(data, "/admin/audit", kind, status, current, all_workspaces=True)
    return shell("Revisionslogg", body, principal, csrf, "admin")
