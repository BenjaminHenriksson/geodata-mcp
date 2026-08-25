# ADR 0010: AGPL-3.0-only (Public Money, Public Code)

- **Status:** Antagen
- **Datum:** 2026-08-25
- **Berör:** hela leveransen (`LICENSE`)

## Kontext

Leveransen sker inom offentlig upphandling (Sundsvalls kommun, UH-2026-159
"Govtech4all Pilot 3 AI och geodata"). Tenderns ursprungliga formulering pekade
mot EUPL/GPLv3. Principen *Public Money, Public Code* (PMPC) säger att programvara
finansierad med offentliga medel ska vara fri och delbar. Systemet är dessutom en
**nätverkstjänst** — körs den för allmänheten utan copyleft som täcker SaaS,
uppstår en lucka där en aktör kan drifta en modifierad kopia utan att dela källkoden.

## Beslut

Hela leveransen licensieras under **AGPL-3.0-only**. Detta är **överenskommet med
köparen** och ersätter tenderns ursprungliga EUPL/GPLv3-lydelse. AGPL:s §13 täcker
nätverksanvändning: den som kör tjänsten åt användare över nätet måste erbjuda dem
motsvarande källkod. `LICENSE` i repo-roten bär den fullständiga texten.

## Konsekvenser

- (+) Håller PMPC-linjen; delning gäller även vid nätverksdrift och förhindrar en
  sluten proprietär fork av en publik tjänst.
- (−) Alla inkluderade beroenden måste vara AGPL-kompatibla; nya beroenden ska
  licensgranskas innan de tas in.
- (−) SAM 3 levereras under en **custom Meta-licens** (inte AGPL-kompatibel i
  vanlig mening) och körs som en separat tjänst bakom ett HTTP-kontrakt — dess
  licens hanteras och granskas separat (ADR 0007), inte som en del av detta beslut.

## Status

Antagen. `LICENSE` = AGPL-3.0-only.
