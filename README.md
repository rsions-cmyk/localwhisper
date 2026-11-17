# PraatTypen

Lokale push-to-talk dicteertool voor macOS. Houd **Fn** ingedrukt, praat, laat los: de
tekst verschijnt op je cursor, in elke app. Vergelijkbaar met Wispr Flow, maar de
spraakherkenning draait volledig op je eigen Mac. Er gaat geen audio het internet op.

```
Fn ingedrukt ─► microfoon opnemen ─► Fn los ─► Whisper (lokaal, MLX) ─► [opschonen] ─► Cmd+V op je cursor
```

Gemeten op een MacBook Air M3 (24 GB), 70 echte dictaten van gemiddeld 11 seconden: de
tekst staat er een mediaan **1,7 s** na het loslaten (90% binnen 4 s, plakken
meegerekend). Stopwoorden als "uh" en "ehm" kwamen in geen enkel dictaat terug, zonder
extra opschoonstap. Hoe dat zit staat in [docs/MODELLEN.md](docs/MODELLEN.md).

## Documentatie

| Document | Waarvoor |
|---|---|
| Deze README | installeren, gebruiken, instellen, problemen oplossen |
| [docs/MODELLEN.md](docs/MODELLEN.md) | welke lokale modellen, de afwegingen, en hoe "uh's" en stotteringen eruit gaan |
| [docs/ARCHITECTUUR.md](docs/ARCHITECTUUR.md) | hoe het in elkaar zit, en de macOS-valkuilen die we onderweg tegenkwamen |
| [docs/NABOUWEN-MET-CLAUDE.md](docs/NABOUWEN-MET-CLAUDE.md) | een opdracht die je zo aan Claude kunt geven om dit na te bouwen of te verbeteren |

## Wat je nodig hebt

- Een Mac met **Apple Silicon** (M1 of nieuwer). MLX, waarop de lokale modellen
  draaien, werkt niet op Intel-Macs.
- macOS 11 of nieuwer. Ontwikkeld en getest op macOS 26.
- **Python 3.11+**. Hier draait 3.13 van python.org. Homebrew-Python kan ook.
- [Homebrew](https://brew.sh) voor `ffmpeg` en eventueel `ollama`.
- Ongeveer **0,5 GB** schijfruimte voor het standaardmodel, plus 1,9 GB als je
  AI-opschoning wilt.

## Installatie

### 1. Code op de goede plek zetten

Zet de map in `~/Developer/praattypen` of een andere map, maar **niet** in `~/Documents`,
`~/Desktop` of `~/Downloads`. Die mappen zijn door macOS afgeschermd (TCC). Daar kan de
app zijn eigen code niet lezen en krijg je `PermissionError: Operation not permitted`.
Vanuit Terminal werkt het dan wél, en dat maakt het extra verwarrend.

### 2. Python-omgeving

```bash
cd ~/Developer/praattypen
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
brew install ffmpeg          # nodig voor 'transcribe <bestand>' en voor Parakeet
```

### 3. Model alvast ophalen (optioneel)

Bij de eerste start downloadt PraatTypen het Whisper-model (~440 MB) van Hugging Face.
Wil je dat vooraf doen, en meteen controleren dat alles werkt:

```bash
say -v Xander "Dit is een test van PraatTypen." -o /tmp/test.aiff
.venv/bin/python -m praattypen.main transcribe /tmp/test.aiff
```

### 4. De app bouwen

```bash
./scripts/build_app.sh
open /Applications/PraatTypen.app
```

Dit maakt `/Applications/PraatTypen.app`, vindbaar via Spotlight ("praattypen"). Het is
een dunne schil: de bundel start de Python-code uit **deze map**. Wijzig je de code, dan
hoef je dus niet opnieuw te bouwen. Herstart de app en hij gebruikt de nieuwe code.
Verplaats je de map, bouw dan wel opnieuw.

Waarom een app en niet gewoon een script? macOS geeft microfoon- en toetsenbordrechten
aan het programma dat erom vraagt. Vanuit Terminal krijgt *Terminal* die rechten, en
daarmee elk ander script dat je daar ooit draait. De bundel vraagt ze op eigen naam
(`nl.praattypen.app`).

### 5. Rechten geven

Systeeminstellingen → Privacy en beveiliging. Zet **PraatTypen** aan in drie lijsten:

| Lijst | Waarvoor | Zonder dit |
|---|---|---|
| **Microfoon** | opnemen | je krijgt stilte, zonder foutmelding |
| **Invoermonitoring** | de Fn-toets zien | de sneltoets doet niets |
| **Toegankelijkheid** | Cmd+V versturen | de tekst blijft op je klembord staan in plaats van geplakt te worden |

Invoermonitoring en Toegankelijkheid zijn **verschillende lijsten**. Je hebt ze allebei
nodig. Herstart PraatTypen nadat je een recht hebt gegeven: een draaiend proces ziet de
wijziging niet.

Controleren kan in `~/Library/Logs/PraatTypen.log`. Bij elke start staat daar:

```
Permissies: microfoon=toegestaan | invoermonitoring=toegestaan | toetsen posten=toegestaan | toegankelijkheid=toegestaan
```

### 6. Fn-toets vrijmaken

Staat de Fn/🌐-toets in Systeeminstellingen → Toetsenbord op "Emoji tonen", "Wijzig
invoerbron" of "Start dicteren", dan doet macOS bij elke druk ook iets. Zet de instelling
voor die toets op **"Doe niets"**.

### 7. Automatisch starten (optioneel)

Systeeminstellingen → Algemeen → Inloggen → `+` → `PraatTypen.app`. Het model wordt dan
één keer per login geladen.

## Gebruik

1. Zet je cursor waar de tekst moet komen.
2. Houd **Fn** ingedrukt. Onderin het scherm verschijnt een rood balkje: *Luistert…*
3. Praat. Laat Fn los. Het balkje wordt oranje (*Transcriberen…*) en daarna staat de
   tekst op je cursor.

Je klembord wordt na het plakken teruggezet. Wat je eerder had gekopieerd raak je dus
niet kwijt.

Waar je ziet dat PraatTypen draait:

- **Dock-icoon.** Klik erop voor het **statusvenster**: status, engine en model,
  permissies, cijfers van vandaag en de laatste 10 dictaten, met per regel een
  **Plak**-knop.
- **Menubalk-icoon** met "plak opnieuw", config, log en stoppen. Zie *Bekende
  problemen*: dat icoon verschijnt op macOS 26 niet.

### Vanuit de terminal

```bash
scripts/praattypen                       # start push-to-talk (zelfde als de app)
scripts/praattypen transcribe opname.m4a # transcribeer een bestand, print de tekst
scripts/praattypen transcribe opname.m4a --paste
scripts/praattypen history               # laatste dictaten en statistiek
scripts/praattypen history --limit 25 --full
scripts/praattypen cache                 # hoeveel ruimte de modellen innemen
scripts/praattypen cache-clean --yes     # oude modelcaches opruimen
```

Wil je gewoon `praattypen` typen, zet dan een alias in `~/.zshrc`:

```bash
alias praattypen="$HOME/Developer/praattypen/scripts/praattypen"
```

Draai je vanuit de terminal, dan heeft *Terminal* de drie rechten nodig in plaats van
PraatTypen.

## Instellingen

PraatTypen leest de eerste config die hij vindt: `--config <pad>`, dan
`~/.config/praattypen/config.yaml`, dan [config.yaml](config.yaml) in deze map. Onbekende
sleutels geven een foutmelding, zodat een tikfout niet stilletjes wordt genegeerd.
Herstart na een wijziging.

De belangrijkste:

| Sleutel | Standaard | Betekenis |
|---|---|---|
| `hotkey` | `fn` | `fn`/`globe`, of via pynput: `f13`, `right_option`, `right_command`, `caps_lock`, één teken |
| `language` | `nl` | taalcode voor Whisper; `auto` laat Whisper zelf raden (trager, en het gaat mis bij korte zinnen) |
| `engine` | `local_whisper` | `local_whisper`, `local_parakeet` of `cloud_groq`; zie [docs/MODELLEN.md](docs/MODELLEN.md) |
| `models.whisper` | `mlx-community/whisper-large-v3-turbo-q4` | elk MLX-Whisper-model van Hugging Face |
| `cleanup.enabled` | `false` | AI-opschoning via een lokaal Ollama-model, zie hieronder |
| `cloud_boost` | `false` | `true` = altijd Groq gebruiken (cloud), ongeacht `engine` |
| `min_speech_seconds` / `min_speech_rms` | `0.3` / `0.0015` | stiltefilter: te korte of te stille opnames worden overgeslagen, anders verzint Whisper tekst |
| `max_recording_seconds` | `900` | noodrem voor een gemiste Fn-loslating; `0` = uit |
| `overlay_enabled`, `menu_bar_enabled`, `dock_icon`, `status_window_enabled` | `true` | de zichtbare onderdelen |
| `history_enabled` / `history_size` | `true` / `200` | bewaart dictaten in `~/Library/Application Support/PraatTypen/history.jsonl` |
| `auto_cache_clean` / `max_model_cache_gb` | `true` / `3.0` | ruimt bij de start oude Whisper/Parakeet-caches op; het actieve model blijft staan |

`paste_method` staat in de config, maar alleen `clipboard` is gebouwd.

### AI-opschoning (optioneel)

Standaard gaat de tekst van Whisper rechtstreeks naar je cursor. Wil je dat een lokaal
taalmodel interpunctie en hoofdletters nakijkt en stopwoorden verwijdert:

```bash
brew install ollama
ollama pull qwen2.5:3b
```

En in `config.yaml`:

```yaml
cleanup:
  enabled: true
  model: "qwen2.5:3b"
```

Dit kost extra tijd per dictaat. Er is ook een risico: dicteer je een vraag, dan kan het
taalmodel die vraag gaan *beantwoorden* in plaats van alleen netjes maken. Lees
[docs/MODELLEN.md](docs/MODELLEN.md) voordat je dit aanzet. Draait Ollama niet, dan plakt
PraatTypen gewoon de ruwe tekst.

## Problemen oplossen

Kijk eerst in het log: `~/Library/Logs/PraatTypen.log`, ook te openen via het
statusvenster. Ruwe Python-uitvoer, zoals tracebacks, staat in
`~/Library/Logs/PraatTypen.out.log`. Dat bestand wordt bij elke start overschreven.

| Wat je ziet | Oorzaak en oplossing |
|---|---|
| Fn doet niets | Invoermonitoring staat uit, of werd gegeven terwijl de app draaide. Aanzetten en de app herstarten. |
| Balkje verschijnt, maar er wordt niets geplakt | Toegankelijkheid staat uit. De tekst staat nog op je klembord: plak met Cmd+V. |
| "Opname overgeslagen: te kort of te stil" | Microfoonrecht ontbreekt (log zegt dan "volledig stil"), of je sprak heel zacht. Verlaag eventueel `min_speech_rms`. |
| Schuifje staat aan, maar het werkt niet | Na een rebuild ziet macOS de app als een andere app. `tccutil reset ListenEvent nl.praattypen.app`, idem `Accessibility` en `Microphone`, en daarna de app herstarten en opnieuw toestaan. |
| `PaErrorCode -9986` in het log | Het audioapparaat wisselde (AirPods aan of uit). PraatTypen leest de apparaten dan opnieuw in en probeert het nog één keer. Blijft het misgaan, herstart dan. |
| `PermissionError: Operation not permitted` | De map staat in Documents, Desktop of Downloads. Verplaatsen en opnieuw bouwen. |
| App hangt, balkje blijft op *Luistert…* | Zie *Bekende problemen*. |

Opnieuw bouwen (`build_app.sh`) is alleen nodig als dat script zelf verandert. Elke
rebuild geeft de app een nieuwe identiteit, en dan moet je de drie rechten opnieuw geven.

## Bekende problemen

- **Af en toe loopt de app vast tijdens een opname.** Gezien op 16 en 17 november 2026:
  de hoofdthread stopt 1 à 2 seconden na het indrukken van Fn, en alleen geforceerd
  stoppen helpt. De oorzaak is nog niet gevonden. Het systeemlog wijst op een conflict
  tussen de audio-thread en Python. Sinds dit gezien is schrijft
  [praattypen/hangwatch.py](praattypen/hangwatch.py) bij een hang van meer dan 8 seconden
  de stack van elke thread naar `~/Library/Logs/PraatTypen.hang.log`. Wacht bij een hang
  dus ruim 10 seconden voordat je de app stopt, en bewaar dat bestand.
- **Het menubalk-icoon verschijnt niet op macOS 26.** De bundel start met een zsh-script
  dat zichzelf met `exec` vervangt door Python. Control Center weigert het icoon dan
  ("process has mismatched pid version"). Dock-icoon en statusvenster werken wel. De
  oplossing is een echt uitvoerbaar bestand (geen script) als `CFBundleExecutable`. Zie
  [docs/ARCHITECTUUR.md](docs/ARCHITECTUUR.md).
- **Parakeet start bij elk dictaat opnieuw.** De Parakeet-engine roept de CLI aan als apart
  proces en laadt het model dus elke keer opnieuw. Daardoor is hij in deze versie trager
  dan nodig.

## Ontwikkelen

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

De tests gebruiken geen echte microfoon, modellen of netwerk. Opnemen, transcriberen en
plakken test je met de hand. `transcribe <bestand>` is daarvoor de snelste route.

De code staat in [praattypen/](praattypen/). Een rondleiding staat in
[docs/ARCHITECTUUR.md](docs/ARCHITECTUUR.md).
