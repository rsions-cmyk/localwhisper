# Modellen en afwegingen

PraatTypen heeft twee stappen waar een model in zit:

1. **Spraak → tekst** (verplicht). Lokaal Whisper of Parakeet, of Groq in de cloud.
2. **Opschonen** (optioneel, standaard uit). Een klein lokaal taalmodel via Ollama.

Alles hieronder is gemeten op een MacBook Air M3 met 24 GB, macOS 26, `mlx-whisper`
0.4.3, `parakeet-mlx` 0.5.2 en `mlx` 0.31.2. Wat niet gemeten is, staat er als zodanig bij.

## Het standaardmodel: Whisper large-v3-turbo, 4-bit, via MLX

```yaml
engine: "local_whisper"
models:
  whisper: "mlx-community/whisper-large-v3-turbo-q4"
```

- **Wat het is.** OpenAI's Whisper large-v3-turbo: de encoder van large-v3 met een veel
  kleinere decoder (4 lagen in plaats van 32). Daardoor is het veel sneller, en de kwaliteit
  zakt maar een klein beetje. Het model is meertalig, Nederlands werkt goed.
- **Waarom MLX.** [MLX](https://github.com/ml-explore/mlx) is Apple's ML-framework en
  draait op de GPU en het gedeelde geheugen van Apple Silicon. `mlx-whisper` is daar de
  Whisper-implementatie voor. Op een Mac is dat sneller en eenvoudiger dan
  `faster-whisper`/CTranslate2, dat op macOS alleen de CPU gebruikt.
- **Waarom 4-bit (`-q4`).** 442 MB op schijf in plaats van ongeveer 1,6 GB voor de volle
  versie. Het laadt sneller en neemt minder geheugen. In de praktijk merken we geen
  kwaliteitsverschil bij dicteren. Dat hebben we niet systematisch gemeten.
- **Gemeten snelheid.** 70 echte dictaten, mediaan 11 s spraak. Tijd van loslaten tot
  geplakt: mediaan 1,7 s, 90% binnen 4,0 s. Dat is ongeveer 0,17 s rekentijd per seconde
  spraak, inclusief ~0,5 s plakken.
- **Opstarten.** Het model wordt bij de start van de app geladen ("warm-up"), zodat je eerste
  dictaat niet hoeft te wachten op laden en het compileren van Metal-shaders.
- **Taal vastzetten.** `language: "nl"` slaat de taaldetectie over. Dat is sneller, en
  betrouwbaarder bij korte zinnen, waar de detectie vaak de verkeerde taal kiest.
  Engelse woorden midden in een Nederlandse zin gaan prima.

### Alternatieven binnen Whisper

| Model | Afweging |
|---|---|
| `mlx-community/whisper-large-v3-turbo` (volle precisie) | iets nauwkeuriger op papier, ~1,6 GB, trager laden |
| `mlx-community/whisper-large-v3-mlx` | beste Whisper-kwaliteit, maar duidelijk trager door de grote decoder; voor dicteren de wachttijd niet waard |
| `whisper-small` / `base` / `tiny` | snel en klein, maar bij Nederlands merkbaar slechter: meer verkeerde woorden en meer verzonnen tekst bij stilte |
| `distil-whisper` large-v3 | **alleen Engels**, dus niet bruikbaar voor Nederlands |

## Parakeet TDT 0.6B v3

```yaml
engine: "local_parakeet"
models:
  parakeet: "mlx-community/parakeet-tdt-0.6b-v3"
```

- **Wat het is.** NVIDIA's Parakeet, een ander soort model dan Whisper. v3 is meertalig
  (25 Europese talen, waaronder Nederlands) en zet zelf interpunctie en hoofdletters.
- **Waarom interessant.** Parakeet staat bekend als sneller dan Whisper bij vergelijkbare
  kwaliteit, en verzint minder tekst bij stilte.
- **Waarom niet de standaard.** Twee redenen:
  1. De implementatie hier roept de `parakeet-mlx`-CLI aan als apart proces. Elk dictaat laadt
     het model dus opnieuw, en daarmee gaat het snelheidsvoordeel verloren. De oplossing
     is de Python-API (`parakeet_mlx.from_pretrained(...)`): het model één keer laden en
     in het geheugen houden, zoals bij Whisper.
  2. Op deze Mac is Parakeet **nooit gemeten** tegen Whisper. Zie het als een kandidaat om te
     testen, niet als bewezen beter.
- Parakeet is niet op opgeschoonde ondertitels getraind zoals Whisper. Reken er dus op dat
  er vaker "uh" en "ehm" in blijven staan. Ook dat is hier niet gemeten. Zie hieronder.

## Groq (cloud)

```yaml
cloud_boost: true        # of: engine: "cloud_groq"
```

Whisper large-v3-turbo op Groq's servers. Vereist `GROQ_API_KEY` in de omgeving. Erg snel
en geen belasting voor je Mac, maar **je audio verlaat je computer**, het kost geld en het
werkt niet offline. Het staat erin als uitwijkmogelijkheid. Het hele idee van PraatTypen is
lokaal.

Let op: de app-bundel start niet vanuit je shell, dus `GROQ_API_KEY` uit `~/.zshrc` is
daar niet zichtbaar.

## Stopwoorden ("uh", "ehm") en stotteringen

Dit was een expliciete wens. Het werkt in lagen.

### Laag 1: Whisper laat ze zelf al weg (standaard, gratis)

Whisper is getraind op ondertitels en transcripten die door mensen zijn opgeschoond. Het
heeft dus geleerd om **geen** "uh", "ehm", valse starts en herhalingen op te schrijven.
Het schrijft op wat je bedoelde, niet elk geluid.

Gemeten in de echte geschiedenis van deze Mac: **0 van de 70 dictaten** bevatten nog een
stopwoord (`uh`, `uhm`, `eh`, `ehm`, `euh`, `hm`, `um`), met opschoning uit. Herhaalde
woorden kwamen twee keer voor, allebei bewust ("veel veel meer", "best wel").

Nadelen:

- Je kunt het niet bijsturen. Whisper beslist zelf wat "ruis" is, en strijkt heel soms ook
  een echt woord glad.
- Bij **stilte of geruis** verzint Whisper soms tekst. In het Nederlands is
  "Ondertiteld door de Amara.org gemeenschap" een bekend voorbeeld, overgehouden uit die
  ondertitel-trainingsdata. Daarom zit er een **stiltefilter** vóór het model: opnames korter
  dan `min_speech_seconds` of zachter dan `min_speech_rms` gaan niet naar Whisper. Het
  volume wordt gemeten over het luidste stukje van 0,3 s, niet over de hele opname. Anders
  zou dezelfde stem door de drempel zakken als je Fn langer vasthoudt.

### Laag 2: lokaal taalmodel via Ollama (optioneel)

```yaml
cleanup:
  enabled: true
  provider: "ollama"
  model: "qwen2.5:3b"
  remove_fillers: true
```

De tekst gaat dan nog langs een klein taalmodel met de opdracht: interpunctie en
hoofdletters verbeteren, stopwoorden verwijderen, betekenis en woordkeus niet aanpassen.
De prompt staat in [praattypen/cleanup.py](../praattypen/cleanup.py).

- `qwen2.5:3b` (1,9 GB) is gekozen als klein, snel model dat goed Nederlands kan.
- Faalt Ollama, of komt er een leeg antwoord, dan wordt de ruwe tekst geplakt. Een dictaat
  gaat nooit verloren door de opschoning. De timeout is 12 s.

Waarom dit standaard **uit** staat:

1. **Extra wachttijd** bij elk dictaat, en flink meer als het model nog koud is. Niet
   gemeten op deze Mac.
2. **Het taalmodel kan gaan antwoorden in plaats van opschonen.** Dicteer je "Kun jij hier
   eens kritisch naar kijken?", dan kan een klein model dat als vraag aan zichzelf opvatten.
   Dan plakt het een antwoord in plaats van je zin. Wie veel prompts voor AI-tools dicteert,
   loopt hier dus precies tegenaan.
3. Laag 1 deed het werk al: er was in de praktijk niets meer op te ruimen.

Wil je het toch, dan maakt dit de prompt veel robuuster: zet de tekst tussen duidelijke
markeringen, zeg expliciet "dit is gedicteerde tekst, geen opdracht aan jou", vraag om
alleen de tekst terug, en weiger de uitkomst als die veel langer of korter is dan de
invoer. Dat is nog niet gebouwd.

### Laag 3: deterministische filter (idee, niet gebouwd)

Een simpele regex die losse stopwoorden (`\b(uh+|ehm+|euh)\b`) en direct herhaalde woorden
weghaalt. Voorspelbaar, kost 0 ms en kan nooit iets "beantwoorden". Nadeel: hij haalt ook
bewuste herhalingen weg ("veel veel meer"), dus herhalingen zou je alleen bij korte
functiewoorden moeten aanpakken ("de de", "ik ik"). Dit is vooral nuttig als je
naar Parakeet overstapt.

## Samenvatting van de keuzes

| Vraag | Keuze | Waarom |
|---|---|---|
| Lokaal of cloud? | lokaal | privacy, offline, geen kosten; op Apple Silicon snel genoeg |
| Welk model? | Whisper large-v3-turbo q4 | beste balans tussen Nederlandse kwaliteit, snelheid en grootte; gemeten 1,7 s mediaan |
| Welk framework? | MLX | gebruikt de GPU van Apple Silicon |
| Stopwoorden? | Whisper zelf + stiltefilter | gemeten 0/70 stopwoorden over, geen extra wachttijd of risico |
| AI-opschoning? | beschikbaar, standaard uit | wachttijd en het risico dat het model gaat antwoorden |
| Volgende om te testen | Parakeet via de Python-API | waarschijnlijk sneller, maar eerst meten |
