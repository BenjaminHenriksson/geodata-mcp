"""Admin service overview and private host-controller client."""
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import ui

LABELS = {
    "running": "Körs", "active": "Körs", "exited": "Stoppad", "inactive": "Stoppad",
    "dead": "Stoppad", "failed": "Fel", "restarting": "Startar om", "activating": "Startar",
    "created": "Skapad", "paused": "Pausad", "unknown": "Okänd",
    "healthy": "OK", "unhealthy": "Fel", "starting": "Startar", "none": "Saknas",
    "unreachable": "Nås inte", "queued": "Väntar", "success": "Utförd", "error": "Misslyckades",
}
ACTIONS = {"start": "Starta", "restart": "Starta om"}


class Unavailable(Exception):
    pass


def request(method, path, payload=None):
    socket = os.environ.get("SERVICE_CONTROL_SOCKET")
    token_file = os.environ.get("SERVICE_CONTROL_TOKEN_FILE")
    if not socket or not token_file:
        raise Unavailable("Tjänsteövervakning är inte konfigurerad.")
    try:
        token = Path(token_file).read_text().strip()
        with httpx.Client(transport=httpx.HTTPTransport(uds=socket), timeout=40,
                          trust_env=False, follow_redirects=False) as client:
            response = client.request(method, "http://control" + path,
                                      headers={"Authorization": "Bearer " + token}, json=payload)
        if response.status_code == 409:
            raise Unavailable("En åtgärd pågår redan. Uppdatera status innan du försöker igen.")
        if response.status_code == 403:
            raise Unavailable("Åtgärden är inte tillåten för tjänsten.")
        response.raise_for_status()
        return response.json()
    except (OSError, httpx.HTTPError, ValueError) as exc:
        raise Unavailable("Tjänsteövervakningen kan inte nås. Försök uppdatera status.") from exc


def badge(value):
    style = ("success" if value in ("healthy", "running", "active", "success") else
             "error" if value in ("unhealthy", "failed", "unreachable", "error") else
             "queued" if value in ("starting", "restarting", "activating", "queued") else "")
    return f'<span class="badge {style}">{ui.e(LABELS.get(value, value))}</span>'


def stamp(value):
    return ui.e(str(value or "—").replace("T", " ")[:19])


def uptime(value):
    if not value:
        return "—"
    try:
        started = datetime.fromisoformat(value.replace("Z", "+00:00"))
        seconds = max(0, int((datetime.now(timezone.utc) - started).total_seconds()))
        days, remaining = divmod(seconds, 86400)
        hours, remaining = divmod(remaining, 3600)
        return f"{days} d {hours} h" if days else f"{hours} h {remaining // 60} min"
    except (ValueError, TypeError):
        return "—"


def service_page(data, principal, csrf, error=None, accepted=False):
    e = ui.e
    body = ('<div class="page-heading"><h1>Tjänster</h1>'
            '<a class="button" href="/admin/services">Uppdatera status</a></div>')
    if error:
        body += f'<p class="alert" role="alert">{e(error)}</p>'
    if accepted:
        body += '<p class="notice" role="status">Åtgärden är köad. Uppdatera status för att följa resultatet.</p>'
    if data:
        body += f'<p class="caption">Kontrollerad {stamp(data["checked_at"])} UTC</p>'
        body += '<div class="services-grid">'
        for s in data["services"]:
            body += ('<section class="panel service"><div class="section-title">'
                     f'<h2>{e(s["name"])}</h2>{badge(s["state"])}</div>'
                     '<dl class="detail-grid">')
            details = [("Typ", "Container" if s["kind"] == "docker" else "Värdtjänst")]
            if s.get("state") in ("running", "active"):
                details.append(("Drifttid", uptime(s.get("started_at"))))
            if s.get("health") not in (None, "none", "unknown"):
                details.append(("Containerhälsa", badge(s["health"])))
            if "api_health" in s:
                details.append(("API-hälsa", badge(s["api_health"])))
            if "latency_ms" in s:
                details.append(("Svarstid", str(s["latency_ms"]) + " ms"))
            if "backend" in s:
                details.append(("Backend", s["backend"]))
            if "model_loaded" in s:
                details.append(("Modell", "Laddad" if s["model_loaded"] else "Ej laddad"))
            if "cpu" in s:
                details.append(("CPU", s["cpu"]))
            if "cpu_seconds" in s:
                details.append(("CPU-tid", f'{s["cpu_seconds"]:.0f} s'))
            if "memory" in s:
                details.append(("Minne", s["memory"]))
            if "memory_bytes" in s:
                details.append(("Minne", f'{s["memory_bytes"] / 1048576:.1f} MiB'))
            if "restarts" in s:
                details.append(("Automatiska omstarter", s["restarts"]))
            body += "".join(f"<dt>{e(k)}</dt><dd>{v if k in ('Containerhälsa', 'API-hälsa') else e(v)}</dd>"
                            for k, v in details) + "</dl>"
            technical = [(k, s.get(v)) for k, v in
                         (("Image", "image"), ("Container", "container_id"), ("Service", "unit")) if s.get(v)]
            if technical:
                body += '<details class="metadata"><summary>Detaljer</summary><dl class="detail-grid">'
                body += "".join(f'<dt>{e(k)}</dt><dd>{e(v)}</dd>' for k, v in technical) + '</dl></details>'
            actions = s.get("actions", [])
            current_action = "start" if s["state"] in ("exited", "inactive", "dead", "failed", "created") else "restart"
            if current_action in actions:
                label = ACTIONS[current_action]
                body += (f'<details class="service-action"><summary>{label}</summary>'
                         '<p class="caption">Pågående arbete kan avbrytas.</p>'
                         '<form method="post" action="/admin/services/action">'
                         f'<input type="hidden" name="csrf" value="{e(csrf)}">'
                         f'<input type="hidden" name="request_id" value="{uuid4()}">'
                         f'<input type="hidden" name="service" value="{e(s["id"])}">'
                         f'<input type="hidden" name="action" value="{current_action}">'
                         f'<button type="submit" class="danger">{label} {e(s["name"])}</button></form></details>')
            else:
                body += '<span class="caption">Övervakning</span>'
            body += '</section>'
        body += '</div><section class="panel"><div class="section-title"><h2>Underhållslogg</h2><span class="caption">UTC</span></div>'
        if data["history"]:
            body += '<div class="table-wrap"><table><thead><tr><th>Tid</th><th>Tjänst</th><th>Åtgärd</th><th>Användare</th><th>Resultat</th></tr></thead><tbody>'
            names = {s["id"]: s["name"] for s in data["services"]}
            for row in data["history"]:
                body += (f'<tr><td>{stamp(row["requested_at"])}</td>'
                         f'<td>{e(names.get(row["service"], row["service"]))}</td>'
                         f'<td>{e(ACTIONS[row["action"]])}</td><td>{e(row["actor"])}</td>'
                         f'<td>{badge(row["status"])}</td></tr>')
            body += '</tbody></table></div>'
        else:
            body += '<p class="empty">Inga underhållsåtgärder.</p>'
        body += '</section>'
    return ui.document("Tjänster", body, principal, csrf, "services")
