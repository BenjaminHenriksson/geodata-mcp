# Observerbarhet: central loggning och övervakning (#81)

Detta dokument beskriver hur Geodata MCP v2 uppfyller krav **#81 – central
loggning och övervakning via standardprotokoll**. Lösningen bygger uteslutande
på öppna, leverantörsneutrala standarder så att drift kan ske i valfri miljö
(Docker Compose, Kubernetes eller Red Hat OpenShift) utan inlåsning:

| Signal | Standard | Transport |
| --- | --- | --- |
| Loggar | Strukturerad JSON, en post per rad ("JSON lines") | `stdout` → containerns loggdrivrutin → central logglagring / Syslog |
| Metrics | Prometheus text-exposition (`text/plain; version=0.0.4`) | HTTP-skrapning av `/metrics` |
| Spårning (tracing) | OpenTelemetry (OTLP) | OTLP/gRPC eller OTLP/HTTP till valfri collector |

Ingen del av lösningen kräver en specifik molnleverantör eller proprietär agent.
All konfiguration sker via miljövariabler.

## 1. Loggning – strukturerad JSON till stdout

Samtliga tjänster (`mcp`, `worker`, `viewer`) skriver loggar till `stdout`.
Enligt principen *"the twelve-factor app"* behandlas loggar som en
händelseström: tjänsten ska aldrig äga sina egna loggfiler eller sköta
rotation. Containerns runtime fångar `stdout` och plattformen vidarebefordrar
strömmen till en central mottagare (t.ex. en Syslog-server, Loki, Elastic eller
OpenShift Logging/Vector).

### Referensimplementation: `viewer`

`viewer`-tjänsten är referensimplementation. Modulen
[`services/viewer/obs.py`](../services/viewer/obs.py) innehåller `init_logging()`
som konfigurerar standardbibliotekets `logging` till att skriva **en JSON-post
per rad**. `main.py` anropar den vid uppstart (per uvicorn-arbetsprocess):

```json
{"ts": "2026-08-25T16:18:46.348Z", "level": "INFO", "logger": "viewer.main", "msg": "viewer started", "service": "viewer", "event": "startup"}
```

Egenskaper som gör formatet lämpligt för central insamling:

- **En post per rad** – varje logghändelse är ett komplett JSON-objekt på egen
  rad, vilket alla vanliga loggdrivrutiner och Syslog-forwarders tolkar som en
  händelse utan specialparser.
- **`service`-fält** – varje rad stämplas med tjänstens namn så att en
  aggregerande insamlare kan filtrera per källa.
- **Kontext bevaras** – fält som skickas med `logger.info(..., extra={...})`
  (t.ex. `view_id`, `query_id`) hamnar som toppnivåfält i JSON och överlever
  hela vägen till den centrala logglagringen.
- **uvicorns egna loggar** (`uvicorn`, `uvicorn.error`, `uvicorn.access`)
  omdirigeras genom samma JSON-hanterare, så att åtkomstloggar följer samma
  format.

Implementationen är **additiv och icke-brytande**: `init_logging()` använder
enbart standardbiblioteket och kan inte fela på importvägen.

### `worker` och `mcp`

`worker` konfigurerar redan `logging` mot `stdout`
([`services/worker/main.py`](../services/worker/main.py)) och `mcp` ärver
`stdout`-loggning från uvicorn/MCP-runtime. Samma stdout-baserade insamling
gäller därmed alla tre tjänsterna redan idag. `obs.py`-mönstret
(JSON-formatteraren) är avsett att återanvändas oförändrat i `worker` och `mcp`
så att alla tre tjänsterna emitterar identiskt JSON-format; det kräver enbart
att respektive tjänst anropar en motsvarande `init_logging()` vid uppstart.

### Miljövariabler (loggning)

| Variabel | Standardvärde | Effekt |
| --- | --- | --- |
| `LOG_LEVEL` | `INFO` | Nivå för rotloggaren (`DEBUG`/`INFO`/`WARNING`/`ERROR`). Okänt värde faller tillbaka till `INFO`. |

## 2. Metrics – Prometheus skrapar `/metrics`

`viewer` exponerar Prometheus-metrics på **`GET /metrics`** i det standardiserade
text-expositionsformatet. Endpointen monteras som en ASGI-underapp i
`main.py`:

```python
app.mount("/metrics", obs.metrics_app(), name="metrics")
```

`metrics_app()` i `obs.py` bygger på biblioteket `prometheus-client`
(tillagt i [`services/viewer/requirements.txt`](../services/viewer/requirements.txt)).
Importen är **skyddad med `try/except`**: om beroendet skulle saknas i imagen
returneras i stället en enkel `text/plain`-hanterare som svarar `200`, så att
tjänsten alltid startar och en skrapning aldrig fäller monteringen.

Utan ytterligare kod exponeras Pythons standardmetrics (process-CPU,
minne, GC, öppna filbeskrivare m.m.). Applikationsspecifika mätvärden kan läggas
till med vanliga `prometheus_client`-`Counter`/`Histogram`-objekt.

### Flera arbetsprocesser

`viewer` körs med flera uvicorn-arbetsprocesser (`--workers 3`). För att en
skrapning ska aggregera värden över **alla** processer sätts
`PROMETHEUS_MULTIPROC_DIR` till en delad, skrivbar katalog; då används
`prometheus-client`:s multiprocess-läge. Utan variabeln exponerar varje
arbetsprocess enbart sitt eget register och skrapningen träffar den process som
råkar svara – tillräckligt för hälsokontroll men inte för exakt aggregering.

### Miljövariabler (metrics)

| Variabel | Standardvärde | Effekt |
| --- | --- | --- |
| `PROMETHEUS_MULTIPROC_DIR` | (osatt) | Delad katalog för multiprocess-aggregering över uvicorn-arbetsprocesser. Sätt vid flera workers. |

### Skrapning i OpenShift (Prometheus Operator)

I OpenShift sköts skrapningen av den inbyggda **Prometheus Operator**. Man
deklarerar en `ServiceMonitor` (eller `PodMonitor`) som pekar ut `viewer`:s
port och sökväg `/metrics`; operatorn genererar då skrapkonfigurationen
automatiskt och plockar upp target löpande. Exempel:

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: geodata-viewer
  labels:
    app: geodata-viewer
spec:
  selector:
    matchLabels:
      app: geodata-viewer
  endpoints:
    - port: http          # porten på viewer-servicen (8001)
      path: /metrics
      interval: 30s
```

Samma mönster (en `ServiceMonitor` per tjänst med `/metrics`) gäller när
`worker` och `mcp` senare exponerar sina egna `/metrics` enligt referens-
implementationen ovan. I Docker Compose-miljö konfigureras i stället en
fristående Prometheus med ett `static_configs`-target mot `viewer:8001/metrics`.

## 3. Spårning (tracing) – OpenTelemetry via OTLP

Distribuerad spårning använder **OpenTelemetry** och exporteras med
standardprotokollet **OTLP** till valfri collector (t.ex. OpenTelemetry
Collector, Grafana Tempo, Jaeger eller Red Hat build of OpenTelemetry i
OpenShift).

Eftersom tjänsterna är FastAPI/uvicorn-baserade aktiveras spårning **utan
kodändring** via OpenTelemetrys auto-instrumentering. Man installerar
`opentelemetry-distro` samt `opentelemetry-instrumentation-fastapi` i imagen och
startar processen genom `opentelemetry-instrument`, varefter beteendet styrs
helt av `OTEL_*`-miljövariabler. Instrumenteringen är **opt-in**: om ingen OTLP-
endpoint anges genereras inga spår och tjänsten påverkas inte.

### Miljövariabler (tracing)

| Variabel | Exempel | Effekt |
| --- | --- | --- |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://otel-collector:4317` | Adress till OTLP-collector. Osatt ⇒ spårning inaktiv. |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc` eller `http/protobuf` | OTLP-transport. |
| `OTEL_SERVICE_NAME` | `geodata-viewer` | Tjänstens namn i spåren (sätt per tjänst: `geodata-mcp`, `geodata-worker`, `geodata-viewer`). |
| `OTEL_TRACES_EXPORTER` | `otlp` | Väljer OTLP-exportören (`none` inaktiverar). |
| `OTEL_TRACES_SAMPLER` | `parentbased_traceidratio` | Samplingsstrategi. |
| `OTEL_TRACES_SAMPLER_ARG` | `0.1` | Samplingsandel (10 %). |
| `OTEL_RESOURCE_ATTRIBUTES` | `service.namespace=geodata,deployment.environment=prod` | Extra resursattribut. |

## 4. Sammanfattning per tjänst

| Tjänst | Loggar (JSON→stdout) | `/metrics` (Prometheus) | Tracing (OTLP) |
| --- | --- | --- | --- |
| `viewer` | Ja – `obs.init_logging()` (referens) | Ja – `obs.metrics_app()` monterad på `/metrics` | Via `OTEL_*` + auto-instrumentering |
| `worker` | Ja – stdout via `logging` | Följer `obs.py`-mönstret | Via `OTEL_*` + auto-instrumentering |
| `mcp` | Ja – stdout via uvicorn/MCP | Följer `obs.py`-mönstret | Via `OTEL_*` + auto-instrumentering |

Alla tre signaltyperna bygger på öppna standarder och konfigureras via
miljövariabler, vilket uppfyller #81:s krav på central loggning och övervakning
via standardprotokoll utan leverantörsinlåsning.
