# Architectuur

Hoe PraatTypen in elkaar zit, en vooral waaróm zo. Bijna elke keuze hieronder is het
antwoord op iets dat in de praktijk misging. Het tweede deel, *macOS-valkuilen*, is het
belangrijkste stuk voor wie dit wil nabouwen.

## Overzicht

```
                main thread (AppKit run loop)                     achtergrond
 ┌──────────────────────────────────────────────────────┐
 │ Quartz event tap ──► _FnKeyState ──► PushToTalkController ──► AudioRecorder (PortAudio)
 │      (Fn-toets)                         │ press/release            │ audio-callback-thread
 │                                         │                          ▼
 │ overlay / menubalk / statusvenster ◄────┤                   Recording (numpy, in geheugen)
 │ (alleen via AppHelper.callAfter)        │                          │
 └─────────────────────────────────────────┼──────────────────────────┼─────────────────────
                                           ▼                          ▼
                               watchdog-thread              TranscriptionWorker-thread
                               (gemiste Fn-loslating,        engine.transcribe_recording()
                                tap weer aanzetten)          → cleanup.clean()
                                                             → ClipboardInjector.inject()  (Cmd+V)
                                                             → history.add()
```

Eén proces, Python 3, met PyObjC voor alles wat macOS-specifiek is. Geen server, geen
achtergronddaemon. De `.app` is een dunne schil om deze map.

## Modules

| Bestand | Verantwoordelijkheid |
|---|---|
| [main.py](../praattypen/main.py) | CLI (`run`, `transcribe`, `history`, `cache`, `cache-clean`) en het aan elkaar knopen van alles in `run()` |
| [hotkey.py](../praattypen/hotkey.py) | Fn-listener (Quartz event tap), `PushToTalkController` (status opnemen/niet opnemen), watchdog |
| [audio.py](../praattypen/audio.py) | opnemen met `sounddevice`, stiltefilter, `Recording` |
| [worker.py](../praattypen/worker.py) | één achtergrondthread met wachtrij: warm-up en transcripties in volgorde |
| [pipeline.py](../praattypen/pipeline.py) | transcript → opschonen → plakken, plus statusmeldingen |
| [engines/](../praattypen/engines/) | `local_whisper`, `local_parakeet`, `cloud_groq` achter één interface (`TranscriptionEngine`) |
| [cleanup.py](../praattypen/cleanup.py) | optionele opschoning via Ollama |
| [inject.py](../praattypen/inject.py) | klembord zetten, Cmd+V via Quartz, klembord terugzetten |
| [overlay.py](../praattypen/overlay.py) | zwevend balkje (Luistert / Transcriberen) |
| [menubar.py](../praattypen/menubar.py) | menubalk-icoon en menu |
| [statuswindow.py](../praattypen/statuswindow.py) | statusvenster met permissies en de laatste dictaten |
| [appearance.py](../praattypen/appearance.py) | Dock-icoon aan/uit, app-menu met Cmd+Q |
| [permissions.py](../praattypen/permissions.py) | echte TCC-status uitlezen (microfoon, invoermonitoring, toegankelijkheid) |
| [history.py](../praattypen/history.py) | dictaten als JSONL in `~/Library/Application Support/PraatTypen/` |
| [storage.py](../praattypen/storage.py) | Hugging Face-modelcache bekijken en opruimen |
| [hangwatch.py](../praattypen/hangwatch.py) | stacks van alle threads dumpen als de main thread langer dan 8 s hangt |
| [logging_setup.py](../praattypen/logging_setup.py) | log naar `~/Library/Logs/PraatTypen.log` (roteert op 1 MB) |
| [config.py](../praattypen/config.py) | `config.yaml` → bevroren dataclasses; onbekende sleutels zijn een fout |
| [scripts/build_app.sh](../scripts/build_app.sh) | bouwt `/Applications/PraatTypen.app` |

## Het verloop van één dictaat

1. **Fn omlaag.** macOS roept de event-tap-callback aan op de main thread.
   `_FnKeyState` ziet de overgang en roept `controller.press()` aan. Die opent de
   microfoonstream. Daarna tonen overlay, menubalk en statusvenster *Luistert*.
2. **Opnemen.** PortAudio roept per blok audio een callback aan op zijn eigen thread. Die
   kopieert de samples in een lijst. Er gaat niets naar schijf.
3. **Fn omhoog.** `controller.release()` sluit de stream en plakt de blokken aan elkaar.
   Daarna gaat de opname door het stiltefilter. Te kort of te stil: overslaan.
4. **Wachtrij.** De opname gaat naar de `TranscriptionWorker`. De callback keert meteen
   terug. Dat moet ook, zie valkuil 2.
5. **Transcriberen.** `LocalWhisperEngine` geeft de numpy-array direct aan `mlx-whisper`.
   Geen WAV en geen ffmpeg. Andere engines schrijven een tijdelijk WAV-bestand en ruimen dat
   daarna zelf op.
6. **Opschonen** (als aan) via Ollama. Faalt dat, dan gaat de ruwe tekst door.
7. **Plakken.** Oud klembord bewaren, tekst erop, 50 ms wachten, Cmd+V, 400 ms wachten,
   oud klembord terug.
8. **Bijwerken.** Geschiedenis, statusvenster, overlay weg.

## macOS-valkuilen (lees dit voordat je iets herbouwt)

Elk punt hieronder heeft ons echt tijd gekost.

### 1. De Fn-toets kan alleen via een Quartz event tap
`pynput` ziet de Fn/Globe-toets niet als gewone toets. Het werkt met een *listen-only*
`CGEventTap` op `kCGEventFlagsChanged`, waarin je de vlag `kCGEventFlagMaskSecondaryFn`
leest. Een listen-only tap valt onder **Invoermonitoring**, niet onder Toegankelijkheid.

### 2. macOS zet je event tap stilletjes uit
Duurt de callback te lang, of komt er *secure input* (wachtwoordveld, vergrendelscherm),
dan schakelt macOS de tap uit (`kCGEventTapDisabledByTimeout` / `...ByUserInput`). Niemand
zet hem weer aan. Gevolg: de sneltoets is dood tot je herstart, en een ingedrukte Fn wordt
nooit losgelaten. Daarom:
- zet de callback de tap meteen weer aan bij dat event,
- doet de callback zo weinig mogelijk (transcriberen gebeurt op de worker),
- meet de callback hoe lang hij duurt (`Fn ingedrukt (123 ms in de tap-callback)` in het
  log). Een microfoon openen kost al ~100 ms,
- leest een **watchdog** elke 2 s de echte toetsenbordstatus
  (`CGEventSourceFlagsState`), buiten de tap om. Is Fn twee keer achter elkaar niet meer
  ingedrukt terwijl we nog opnemen, dan rondt hij de opname alsnog af.

### 3. Plakken via Quartz, niet via pynput
`pynput` vertaalt "v" naar een keycode via HIToolbox, en dat moet op de main thread
gebeuren. Vanuit Terminal ging dat toevallig goed. Binnen een app-bundel, vanaf de worker-thread,
**doodde macOS het hele proces met SIGTRAP** midden in het plakken. Nu: `CGEventPost` met
vaste virtuele keycodes (`kVK_ANSI_V` = 0x09, `kVK_Command` = 0x37). Die zijn
layout-onafhankelijk en thread-veilig. Command wordt als echte toets ingedrukt, niet alleen
als vlag, want Electron- en Java-apps letten op het toetsevent zelf. Zet Command ook altijd
weer los in een `finally`.

### 4. Zonder Toegankelijkheid verdwijnt je tekst
Zonder Toegankelijkheid negeert macOS je Cmd+V **zonder foutmelding**. Zet je daarna het
oude klembord terug, dan is het dictaat weg. Daarom controleert `inject()` eerst
`AXIsProcessTrusted()`. Is dat niet goed, dan blijft de tekst op het klembord staan en zegt het log dat.

### 5. Rechten horen bij een identiteit, en die is lastig
- Draai je `python` vanuit Terminal, dan krijgt Terminal de rechten.
- Draai je de framework-interpreter (`Python.framework/.../Python`) rechtstreeks vanuit een
  bundel, dan meldt elk PyObjC-proces zich als `org.python.python`, en landen de rechten bij
  "Python", gedeeld met elke Python op je Mac.
- Oplossing: **kopieer de interpreter de bundel in** (`Contents/MacOS/praattypen-python`) en
  geef `PYTHONHOME` en `PYTHONPATH` expliciet mee. Dan is het proces `nl.praattypen.app`.
- Ondertekenen gebeurt ad hoc (`codesign --sign -`). Elke rebuild geeft een andere
  handtekening, dus een andere app voor macOS, terwijl het schuifje in Systeeminstellingen
  gewoon aan blijft staan. Bouw dus zo min mogelijk opnieuw, en gebruik
  `tccutil reset <dienst> nl.praattypen.app` als het schuifje "aan" staat maar niets doet.
- Een recht dat je geeft terwijl de app draait, ziet de app pas na een herstart.

### 6. Afgeschermde mappen
Staat de code in `~/Documents`, `~/Desktop` of `~/Downloads`, dan mag de bundel zijn eigen
`.py`-bestanden niet lezen (`PermissionError`). Vanuit Terminal werkt het wel. Zet de
checkout ergens anders, zoals `~/Developer`.

### 7. Opstarten zonder focus te stelen, mét Dock-icoon
Een dicteertool die bij het inloggen de voorgrond-app overneemt, plakt in het verkeerde
venster. De bundel is daarom `LSUIElement` (start zonder focus) en promoveert zichzelf daarna
in code tot `NSApplicationActivationPolicyRegular` voor het Dock-icoon. Bouw dan ook een
hoofdmenu, anders werkt Cmd+Q niet en is de menubalk leeg.

### 8. AppKit alleen op de main thread
Overlay, menubalk en statusvenster worden op de main thread gemaakt. Alle updates vanuit
andere threads gaan via `PyObjCTools.AppHelper.callAfter`. De main thread draait
`NSApplication.run()`. Dat houdt ook de event tap levend, omdat de tap-bron in
`kCFRunLoopCommonModes` staat.

### 9. Een menubalk-item moet je vanuit de run loop bouwen
Maak je een `NSStatusItem` aan voordat de run loop draait, dan krijg je een item dat zegt
dat het zichtbaar is, terwijl de window server er nooit van hoort. Geen icoon, geen fout.
Daarom staat het in `AppHelper.callLater(0.0, ...)`.

### 10. Op macOS 26 mag het bundel-uitvoerbare bestand geen `exec`-script zijn (open probleem)
Het `CFBundleExecutable` is nu een zsh-script dat zichzelf met `exec` vervangt door de
interpreter. Hetzelfde pid, maar een nieuwe *pid-versie*. Control Center host op macOS 26
de menubalk-items, kan het proces dan niet meer koppelen ("Unable to resolve vpid …
process has mismatched pid version"), en het icoon verschijnt nooit. AppKit probeert het
elke seconde opnieuw. **Oplossing voor een herbouw:** maak het bundel-uitvoerbare bestand een
echt programma, bijvoorbeeld een kleine C- of Swift-launcher die Python embedt of
`posix_spawn` gebruikt in plaats van `exec`, of gebruik py2app of Briefcase.

### 11. PortAudio onthoudt het oude audioapparaat
`sounddevice`/PortAudio leest de lijst met apparaten één keer in. Wisselt je microfoon
(AirPods aan of uit), dan faalt elke opname met `PaErrorCode -9986` tot je herstart.
`AudioRecorder.start()` probeert het daarom na een fout opnieuw na
`sd._terminate(); sd._initialize()`.
Een stream die niet goed sluit (slaapstand, vergrendelen) wordt weggegooid, en de al
opgenomen audio blijft bewaard.

### 12. Whisper verzint tekst bij stilte
Zie [MODELLEN.md](MODELLEN.md). Het stiltefilter meet het luidste venster van 0,3 s, niet
het gemiddelde van de hele opname. Volledig stil (exact 0.0) betekent bijna altijd dat het
microfoonrecht ontbreekt. Het log zegt dat dan ook.

### 13. Het eerste dictaat mag niet op het model wachten
`mlx-whisper` laadt het model pas bij de eerste `transcribe`. Daarom zet `run()` meteen een
warm-up (0,5 s stilte) op de worker. Een dictaat tijdens de warm-up wacht netjes in de
rij en start geen tweede keer laden.

### 14. Open: af en toe hangt de main thread vlak na de start van een opname
Gezien op 16 en 17 november 2026, twee keer in ongeveer 70 dictaten. De microfoon gaat open,
1 à 2 s later stopt de main thread, en CoreAudio meldde bij de start
`HALC_ProxyIOContext::IOWorkLoop: skipping cycle due to overload`. Bij een gezonde opname
staat die melding er niet. Waarschijnlijk wacht de audio-callback-thread op de Python-GIL
terwijl de main thread vastzit. Dat is nog niet bewezen. `hangwatch.py` dumpt bij de
volgende keer de stack van elke thread naar `~/Library/Logs/PraatTypen.hang.log`. Die
dump komt van faulthandler, dat de GIL niet nodig heeft. Kandidaten voor een
structurele oplossing:
- de microfoonstream openen en sluiten **buiten** de event-tap-callback, op een eigen thread,
- één permanente stream openhouden en alleen aan- of uitzetten of je samples bewaart. Dat
  kost wel een microfoonlampje dat altijd brandt,
- de audio-callback zonder Python laten draaien (een ringbuffer in C of Swift).

## Bestanden die PraatTypen schrijft

| Pad | Inhoud |
|---|---|
| `~/Library/Logs/PraatTypen.log` | statusregels en fouten (roteert) |
| `~/Library/Logs/PraatTypen.out.log` | ruwe stdout/stderr, per start overschreven |
| `~/Library/Logs/PraatTypen.hang.log` | stacks bij een hang of harde crash |
| `~/Library/Application Support/PraatTypen/history.jsonl` | je dictaten (privé!) |
| `~/.cache/huggingface/hub/models--mlx-community--…` | modellen |
| `$TMPDIR/praattypen-*.wav` | alleen kortstondig, bij Parakeet en Groq |

## Tests

`pytest` dekt de logica zonder hardware: de toestanden van de controller (dubbel drukken,
fout bij starten of stoppen), de watchdog, het stiltefilter, klembord en plakken met nep-functies,
config-validatie, geschiedenis, cache-opruimen, de engines met nep-modellen, en het
build-script zelf. Een test controleert bijvoorbeeld dat `build_app.sh` geen `/Users/`-pad
bevat en de interpreter-kopie behoudt.
