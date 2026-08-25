# Tillgänglighetsutlåtande – Geodata MCP (pilot)

**Standard:** WCAG 2.1 nivå AA (samt EN 301 549)
**Typ:** Självskattning under pågående pilot
**Senast uppdaterad:** 2026-08-25
**Upphandling:** UH-2026-159 "Govtech4all Pilot 3 – AI och geodata", Sundsvalls kommun
**Funktionellt krav:** #43 (tillgänglighet), i samverkan med #3 och #45

Detta utlåtande beskriver hur webbgränssnittet i Geodata MCP (tjänsten
`services/viewer`) förhåller sig till Webbriktlinjernas krav och till WCAG 2.1
nivå AA. Utlåtandet är en självskattning som gjorts av utvecklingsteamet inom
ramen för piloten. Ingen fullständig, oberoende granskning har ännu
genomförts (se avsnittet *Kända brister* och *Plan*).

## Omfattning

Utlåtandet omfattar de webbsidor som `services/viewer` genererar och som en
mänsklig användare kan möta i webbläsare:

- **Kartvyn (MapLibre)** – standardrenderaren för en delad vy (`/v/<id>`).
- **Kartvyn (Origo/OpenLayers)** – alternativ renderare
  (`/v/<id>?renderer=origo`).
- **Inloggning** – arbetsytehanterarens inloggningssida (`/login`).
- **Arbetsytor** – översikts- och hanteringssidan för arbetsytor
  (`/workspaces`).

Utlåtandet omfattar **inte** MCP-kontrollplanet, API:er eller de
maskin-till-maskin-gränssnitt som saknar mänskligt användargränssnitt, och
inte heller kart- och basdata som tillhandahålls av tredje part (t.ex.
ortofoton och bakgrundskartor).

## Efterlevnadsstatus

Tjänsten bedöms vara **delvis förenlig** med WCAG 2.1 nivå AA. De mänskliga
gränssnitten uppfyller merparten av de tillämpliga kriterierna, men det finns
kända brister – framför allt kopplade till den interaktiva kartrenderingen
(WebGL/canvas) och till tredjepartskomponenternas inbyggda kontroller.

## Vad som uppfylls

Följande har åtgärdats och bedöms uppfyllt i pilotens gränssnitt:

- **Språk (WCAG 3.1.1, nivå A):** Samtliga sidor deklarerar `lang="sv"` och
  allt gränssnittsinnehåll (etiketter, knappar, platshållare, titlar,
  verktygstips och meddelanden) är på svenska.
- **Sidtitlar (WCAG 2.4.2, nivå A):** Varje sida har en beskrivande
  `<title>` (t.ex. "Kartvy", "Arbetsytor", "Arbetsytehanterare – logga in").
- **Hoppa till innehåll (WCAG 2.4.1, nivå A):** Varje sida inleds med en
  synlig-vid-fokus "Hoppa till innehåll"-länk som leder till huvudinnehållet.
- **Semantisk struktur (WCAG 1.3.1, nivå A):** Landmärken används –
  `<main>` för huvudinnehåll (kartvyerna via `role="main"` på kartcontainern),
  `<header>` för sidhuvud på arbetsytesidan, och `role="region"` med
  `aria-label` för teckenförklaring och bildlagerpanel.
- **Etiketter och namn (WCAG 1.3.1, 3.3.2, 4.1.2):** Varje formulärfält har
  en programmatiskt kopplad `<label>` eller `aria-label`
  (lösenordsfältet för API-nyckel, fältet för nytt arbetsytenamn samt
  opacitetsreglaget i kartinspektorn).
- **Meningsfulla länk- och knappnamn (WCAG 2.4.4, 4.1.2):** Renderarväxlaren
  ("⇄ Origo"/"⇄ MapLibre") och stäng-knappen i aviseringsrutan har
  beskrivande `aria-label`/`title`, så att de har ett urskiljbart namn även
  när de visas som ikon/symbol.
- **Statusmeddelanden (WCAG 4.1.3, nivå AA):** Fel- och statusytor använder
  `role="alert"` respektive `role="status"`/`aria-live="polite"` så att
  laddnings-, fel- och uppdateringsmeddelanden aviseras för skärmläsare.
- **Tangentbord (WCAG 2.1.1, delvis):** Formulärkontroller, länkar och
  panelknappar (kart/före/efter, opacitet, förändringskandidater) nås och
  aktiveras med tangentbord. Kartan kan även växla ortofoto före/efter med
  blanksteg.
- **Viewport och skalning (WCAG 1.4.4, 1.4.10):** Samtliga sidor behåller
  `<meta name="viewport">` utan att blockera zoomning, och sidlayouterna
  använder relativa mått.

## Kända brister

Följande brister är kända och ännu inte fullt åtgärdade:

1. **Interaktiv karta (canvas/WebGL).** Själva kartytan renderas i
   `<canvas>` av MapLibre GL respektive Origo/OpenLayers. Kartans geometrier
   är inte exponerade i tillgänglighetsträdet, kan inte navigeras
   feature-för-feature med tangentbord, och saknar ett likvärdigt
   textalternativ (WCAG 1.1.1). Detta är en känd begränsning i de
   webbaserade kartbiblioteken. Data finns dock tillgänglig som strukturerad
   export (GeoPackage/GeoJSON/CSV) via MCP-verktygen som ett alternativ.
2. **Tredjepartskontroller.** MapLibres navigeringskontroll (zoom/kompass)
   och Origos verktygsfält levererar egna knappar och verktygstips på
   engelska och med enbart ikoner. Dessa är inte översatta eller fullt
   granskade för namn/roll/värde (WCAG 4.1.2) i piloten.
3. **Kontrast (WCAG 1.4.3).** Vissa sekundära texter i kartöverläggen (t.ex.
   den grå hjälptexten i inspektorn) kan understiga kontrastkravet 4.5:1 mot
   sin halvtransparenta bakgrund. Detta behöver mätas och justeras.
4. **Fokusordning och synligt fokus (WCAG 2.4.3, 2.4.7).** Fokusordningen i
   kartkontrollernas overlay-paneler och fokusmarkeringens synlighet är inte
   fullständigt verifierade i alla webbläsare.
5. **Dynamiska popup-tabeller.** Attributtabeller som öppnas vid klick i
   kartan är inte fullständigt utvärderade för skärmläsare (fokushantering
   och läsordning).
6. **Ingen oberoende granskning.** Utlåtandet bygger på manuell
   självskattning. Automatiserad testning, granskning med skärmläsare och
   extern expertgranskning återstår att genomföra och dokumentera.

## Plan och prioritering

Följande åtgärder planeras för att höja efterlevnaden mot WCAG 2.1 AA. Ordningen
speglar prioritet; tidpunkter fastställs tillsammans med Sundsvalls kommun inom
pilotens ramar.

- **Textalternativ till kartan.** Erbjuda en tillgänglig, tabellbaserad vy
  och/eller nedladdning av visade lager som ett likvärdigt alternativ till
  canvas-kartan (adresserar 1.1.1 för kartinnehållet).
- **Översätta och granska tredjepartsetiketter.** Sätta svenska
  verktygstips/aria-etiketter på MapLibres och Origos kontroller där
  biblioteken tillåter konfiguration; annars dokumentera avvikelsen.
- **Kontrastöversyn.** Mäta kontrasten i samtliga överläggselement och höja
  otillräckliga värden till minst 4.5:1 (3:1 för stor text).
- **Tangentbords- och fokusgranskning.** Verifiera fokusordning, synligt
  fokus och tangentbordsfällor i kart-overlays och popup-tabeller.
- **Oberoende granskning.** Genomföra granskning med skärmläsare
  (t.ex. NVDA/VoiceOver) och automatiserad testning, samt vid behov extern
  expertgranskning, och uppdatera detta utlåtande med resultatet.

## Utvärderingsmetod

Bedömningen har gjorts genom manuell självskattning av utvecklingsteamet:
granskning av den genererade HTML-koden mot WCAG 2.1 AA-kriterierna,
kontroll av landmärken, etiketter, språkdeklaration, sidtitlar och
tangentbordsåtkomst. Denna metod är en självdeklaration och ersätter inte en
formell, oberoende tillgänglighetsgranskning.

## Återkoppling och kontakt

Om du upptäcker brister i tjänstens tillgänglighet, eller behöver innehåll i
ett tillgängligt alternativt format, meddela detta via den ordinarie
kontaktvägen till Sundsvalls kommun för Govtech4all-piloten. Synpunkter tas
emot löpande och används för att prioritera åtgärderna i planen ovan.

Om du inte är nöjd med hur dina synpunkter hanteras kan du kontakta
Myndigheten för digital förvaltning (DIGG), som utövar tillsyn över lagen om
tillgänglighet till digital offentlig service.
