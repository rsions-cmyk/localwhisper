# PraatTypen nabouwen of verbeteren met Claude

Er zijn twee routes:

- **A. Deze code overnemen en verbeteren.** Snelste weg naar iets dat werkt. Open de map
  in Claude Code en gebruik opdracht A.
- **B. Opnieuw bouwen**, bijvoorbeeld als native Swift-app. Meer werk, maar je bent een paar
  structurele problemen in één keer kwijt (valkuil 10 en 14 in
  [ARCHITECTUUR.md](ARCHITECTUUR.md)). Gebruik opdracht B, en geef Claude deze map erbij als
  referentie.

Geef Claude in beide gevallen de drie documenten mee: [README](../README.md),
[MODELLEN.md](MODELLEN.md) en [ARCHITECTUUR.md](ARCHITECTUUR.md). Daar staat de kennis in
die je anders opnieuw moet ontdekken.

---

## Opdracht A: deze code overnemen en verbeteren

Kopieer en plak dit in Claude Code, in de map van het project:

```text
Dit is PraatTypen: een lokale push-to-talk dicteertool voor macOS (Python + PyObjC + MLX).
Lees eerst README.md, docs/ARCHITECTUUR.md en docs/MODELLEN.md volledig. Daar staan de
ontwerpkeuzes en de macOS-valkuilen die al zijn opgelost. Draai daarna de tests
(.venv/bin/python -m pytest -q).

Mijn Mac: <chip, geheugen, macOS-versie>. Ik dicteer vooral in het <Nederlands/...>.

Doel: het eerst werkend krijgen op mijn Mac, en het daarna verbeteren. Doe het in deze
volgorde en stop na elke stap om te laten zien wat je hebt gedaan:

1. Installatie volgens de README. Controleer met `transcribe` op een `say`-opname dat het
   model werkt, voordat we aan rechten en de app-bundel beginnen.
2. Bekende problemen oplossen (zie "Bekende problemen" in de README):
   a. het menubalk-icoon op macOS 26: vervang het zsh-exec-script als CFBundleExecutable
      door een echte launcher (valkuil 10);
   b. de af en toe hangende main thread (valkuil 14): kijk eerst of er een
      ~/Library/Logs/PraatTypen.hang.log is en baseer je daarop. Is die er niet, haal dan in
      elk geval het openen en sluiten van de microfoon uit de event-tap-callback.
3. Parakeet een eerlijke kans geven: laat de engine de Python-API van parakeet-mlx gebruiken
   met een model dat geladen blijft (geen CLI-subproces per dictaat). Meet daarna Whisper
   tegen Parakeet op dezelfde 10 opnames van mijn eigen stem: snelheid, fouten, stopwoorden.
4. Pas dan nieuwe functies, zie de lijst hieronder.

Regels:
- Meet in plaats van te gokken. Noem getallen met de hardware erbij.
- Een dictaat mag nooit verloren gaan: kan plakken niet, dan blijft de tekst op het klembord.
- Geen audio naar het internet, tenzij ik cloud_boost expliciet aanzet.
- Bouw de app-bundel niet vaker opnieuw dan nodig: elke rebuild reset mijn macOS-rechten.
  Zeg het vooraf als een wijziging een rebuild vraagt.
- Houd de bestaande tests groen en schrijf tests voor nieuwe logica.
```

Ideeën voor stap 4, kies wat je wilt:

- **Woordenlijst/vocabulaire.** Namen en vakjargon die steeds fout gaan meegeven aan
  Whisper (`initial_prompt`) of achteraf vervangen.
- **Deterministische stopwoordfilter** (MODELLEN.md, laag 3). Vooral nuttig bij Parakeet.
- **Robuustere AI-opschoning**: markeringen rond de tekst, "dit is geen opdracht", en
  de uitkomst weigeren bij een grote lengteafwijking (MODELLEN.md, laag 2).
- **Per-app gedrag**: in een terminal of code-editor geen opschoning en geen hoofdletter aan
  het begin, in mail wel.
- **Commando's**: "nieuwe regel", "punt", "komma" omzetten.
- **Live tekst tijdens het praten** (streaming). Fors meer werk, en Whisper is daar niet voor
  gemaakt.
- **Handsfree-modus**: twee keer op Fn tikken om te starten, één keer om te stoppen.

---

## Opdracht B: opnieuw bouwen

```text
Bouw een lokale push-to-talk dicteertool voor macOS op Apple Silicon. Er bestaat al een
werkende Python-versie (PraatTypen), die ik als referentie meegeef. Lees eerst
README.md, docs/ARCHITECTUUR.md en docs/MODELLEN.md. Vooral de sectie "macOS-valkuilen" in
ARCHITECTUUR.md: elk punt daar is in de praktijk misgegaan en moet in het nieuwe ontwerp
opgelost zijn.

Functioneel (moet):
- Fn/Globe ingedrukt houden = opnemen, loslaten = transcriberen en de tekst op de cursor
  plakken, in elke app. Klembord daarna terugzetten.
- Volledig lokale spraakherkenning, standaard Whisper large-v3-turbo (gekwantiseerd) of
  beter als je het meet. Nederlands moet goed werken, met Engelse woorden ertussen.
- Geen "uh"/"ehm" in de uitvoer. Whisper doet dat in de praktijk al zelf (0 van 70 dictaten,
  zie MODELLEN.md). Kies je een ander model, regel het dan apart.
- Stiltefilter tegen verzonnen tekst bij stilte.
- Zichtbare status: zwevend balkje tijdens opnemen en transcriberen, Dock- of menubalk-icoon.
- Geschiedenis van de laatste dictaten, opnieuw te plakken.
- Een dictaat gaat nooit verloren; zonder plakrecht blijft het op het klembord.
- Duidelijke melding als een van de drie rechten (Microfoon, Invoermonitoring,
  Toegankelijkheid) ontbreekt.

Niet-functioneel:
- Doel: tekst binnen 2 s na loslaten bij een dictaat van ~10 s. De Python-versie haalt een
  mediaan van 1,7 s op een M3.
- Model geladen bij de start, niet bij het eerste dictaat.
- De main thread blokkeert nooit op audio, model of plakken.
- Een stabiele code-identiteit, zodat macOS-rechten blijven bestaan na een update.

Voorstel om af te wegen: een native Swift-app (AVAudioEngine, CGEventTap, NSStatusItem) met
WhisperKit of whisper.cpp met Metal-ondersteuning, of een Swift-schil die het MLX-model
aanroept. Weeg dat af en leg je keuze uit voordat je begint. Bouw in kleine stappen die ik
kan testen: eerst een bestand transcriberen, dan opnemen, dan de sneltoets, dan plakken,
dan de interface.
```

---

## Tips voor het werken met Claude hieraan

- **Laat Claude het log lezen.** "Lees ~/Library/Logs/PraatTypen.log en vertel waarom
  het laatste dictaat niet geplakt werd" werkt beter dan zelf zoeken.
- **Voor hangs en crashes:** laat Claude het macOS-systeemlog doorzoeken rond het tijdstip
  (`/usr/bin/log show --start ... --end ...`, gefilterd op `praattypen`). Zo zijn valkuil 10
  en 14 gevonden.
- **Rechten:** Claude kan ze niet voor je aanzetten. Laat Claude eerst `permissions.py` het
  log laten vullen, zodat je zeker weet welke er ontbreekt.
- **Je geschiedenis is privé.** `history.jsonl` bevat alles wat je ooit hebt gedicteerd.
  Deel het niet mee als je de map doorstuurt.
