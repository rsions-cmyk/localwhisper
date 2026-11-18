# AirQuote

Push-to-talk dictation for the Mac that runs entirely on your own computer. Hold
**Right Option ⌥**, speak, let go: the text appears at your cursor, in any app.
No audio leaves your Mac.

```
hold key ─► microphone ─► let go ─► Whisper (on this Mac, MLX) ─► [mode] ─► pasted at your cursor
```

Based on [PraatTypen](https://github.com/sebastiaanvankemenade-commits/localwhisper) by
Sebastiaan van Kemenade. The background documents in [docs/](docs/) are from that
project and in Dutch.

## What it does

- **Dictate** into any app: hold the hotkey, or **double-tap** it for hands-free and tap
  again (or click the floating bar) to stop. The window also has a **Start Dictation**
  button.
- **Dutch and English**: each dictation is detected as one or the other.
- **Modes** (in the window or the menu bar):

  | Mode | What gets pasted | Needs |
  |---|---|---|
  | Dictate | exactly what you said | – |
  | → English | an English translation of what you said | – (Whisper itself) |
  | → Dutch | a Dutch translation | Ollama |
  | Feedback | your spoken remarks as structured, constructive student feedback | Ollama |
  | Rewrite | your selected (or just-copied) text, changed as you asked: "make this more academic" | Ollama |

- **Voice commands** (optional; tick *Voice commands* in the window): say what to do at the
  start of a dictation, in any mode. Dutch and English both work:

  | Start with | What gets pasted |
  |---|---|
  | "Translate to English: …", "Vertaal naar het Nederlands: …" (also German, French, Spanish, Italian, Portuguese) | the rest, translated |
  | "Formal: …", "Informal:", "Academic:", "Friendly:", "Polite:", "Professional:", "Shorter:", "Email:" (or Dutch: "Formeel:", "Korter:", …) | the rest, in that style |
  | "In a cheerful tone: …", "In een zakelijke toon: …" | the rest, in that tone |
  | "Feedback: …" | structured student feedback |
  | "Rewrite this …", "Make this …", "Herschrijf …", "Maak dit …" | the **selected** text, changed as you asked |
  | "AirQuote, …" | whatever you ask: "AirQuote, write a polite reply saying I'll be late" |

  Say a command without anything after it ("Translate to English.") to apply it to the
  selected or just-copied text. "Translate this to English and make it more commercial"
  works as one instruction. To keep ordinary dictation from being taken as a command, a
  one-word command ("Formal", "Feedback") needs a short pause after it, and "Rewrite this /
  Make this" only counts while text is selected. Needs Ollama; the bar shows what was done
  ("✓ Pasted · → English").
- **Vocabulary**: names and jargon it should always spell your way (BUas, ILO, …).
- **Recordings**: drop an interview, meeting or lecture into the window, or record one
  there. You get a transcript with timestamps and, optionally, **speakers told apart**
  (`[00:05] Speaker B: …`), saved next to the recording. Imports into ATLAS.ti, NVivo
  and MAXQDA as plain text.
- **Models**: shows your Mac, recommends the speech model that suits it, downloads
  others, and has an on-demand speed test.

Everything is processed on your Mac. The optional AI modes use [Ollama](https://ollama.com),
which also runs locally.

## What you need

- A Mac with **Apple Silicon** (M1 or newer). MLX does not run on Intel Macs.
- **macOS 13** or newer. Developed on macOS 26.
- **Python 3.11+** and **[Homebrew](https://brew.sh)**.
- **Disk**: about 1 GB for the app's Python packages (2 GB with speaker separation),
  plus 0.5–3 GB for a speech model.
- Optional: **Ollama** with a model such as `gemma4:12b` (8 GB) for → Dutch, Feedback and
  Rewrite. On a Mac with 8–16 GB memory, pick a smaller model.
- Optional: a free **Hugging Face** account for speaker separation (see below).

## Install

1. Put the code in a normal folder, **not** in Documents, Desktop, Downloads, iCloud or
   OneDrive. macOS shields those folders and the app could not read its own code there.

   ```bash
   git clone https://github.com/sebastiaanvankemenade-commits/localwhisper.git ~/Developer/airquote
   ```

2. Check that your Mac is ready (changes nothing):

   ```bash
   cd ~/Developer/airquote && ./install.sh --check
   ```

3. Install. The script asks whether to add speaker separation (`--speakers` or
   `--no-speakers` decides up front):

   ```bash
   ./install.sh
   ```

   It installs ffmpeg if needed, sets up Python, downloads the speech model recommended
   for your Mac, builds `/Applications/AirQuote.app`, opens it, and opens the right pages of
   System Settings.

4. **Give three permissions.** In System Settings → Privacy & Security, switch AirQuote on
   under:

   | List | Why | Without it |
   |---|---|---|
   | **Input Monitoring** | to see the hotkey | the hotkey does nothing |
   | **Accessibility** | to paste with Cmd+V | the text stays on your clipboard |
   | **Microphone** | to record | macOS asks the first time you dictate: click Allow |

   Not listed? Click **+** and choose Applications → AirQuote. Then **quit AirQuote and open
   it again**: a running app does not see new permissions.

5. Hold **Right Option ⌥**, speak, let go.

### Optional extras

- **AI modes** (→ Dutch, Feedback, Rewrite):

  ```bash
  brew install ollama
  ```
  ```bash
  ollama pull gemma4:12b
  ```

  Without Ollama these modes paste the plain transcript, and the floating bar says
  "Plain text · AI off".

- **Speaker separation** (Recordings tab): install with `./install.sh --speakers`, accept the
  model's terms once at
  [huggingface.co/pyannote/speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1),
  then log in once:

  ```bash
  .venv/bin/hf auth login
  ```

- **Start at login**: System Settings → General → Login Items → **+** → AirQuote.

## Using it

1. Put your cursor where the text should go.
2. Hold **Right Option ⌥** and speak. The floating bar shows a waveform and a timer. If a
   mode other than Dictate is on, it says which ("0:03 · Feedback").
3. Let go. The bar shows "Transcribing…" and then "✓ Pasted".

Your clipboard is put back after pasting. Click the **Dock icon** for the window: mode,
today's numbers, recent dictations (copy or paste again), Recordings, Vocabulary, Models
and Setup.

Hands-free: **double-tap** the hotkey, speak as long as you like, then tap once or click
the floating bar. The click does not take focus away from the app you are typing in.

## Settings

Your settings are in `~/.config/praattypen/config.yaml`. `install.sh` creates it from the
[config.yaml](config.yaml) in this folder, which explains every option. Restart AirQuote
after a change. The most useful ones:

| Setting | Default | |
|---|---|---|
| `hotkey` | `right_option` | also `right_command`, `right_control`, `left_option`, `fn`, `f13`, … |
| `language` | `nl,en` | detect between these; `en` fixes one language; `auto` lets Whisper choose any |
| `models.whisper` | `auto` | the model recommended for this Mac, or a Hugging Face repo id |
| `assistant_model` | `gemma4:12b` | Ollama model for the AI modes |
| `sounds_enabled`, `sound_volume` | `true`, `0.25` | soft sounds on start, stop and paste |
| `history_enabled` | `true` | keep recent dictations for the window; `false` keeps nothing on disk |

What is stored: the text of your last 200 dictations
(`~/Library/Application Support/PraatTypen/history.jsonl`), and your vocabulary and chosen
mode in the same folder. The audio of dictations is never saved. Recordings you make in the
window are saved to `~/Documents/AirQuote`.

## Troubleshooting

The log is `~/Library/Logs/PraatTypen.log` (the window's **Log** button opens it).
Python errors go to `~/Library/Logs/PraatTypen.out.log`. Log messages are in Dutch.

| What you see | Cause and fix |
|---|---|
| The hotkey does nothing | Input Monitoring is off, or was switched on while AirQuote was running. Switch it on and restart AirQuote. Meanwhile, use **Start Dictation** in the window. |
| The bar appears but nothing is pasted | Accessibility is off. The text is still on your clipboard: paste with Cmd+V. |
| "te kort of te stil" in the log | Microphone permission is missing, or you spoke very softly. |
| Something other than what you said is pasted | A mode other than Dictate is on (the bar names it), or your dictation started with a voice command (the bar says "✓ Pasted · Formal" and so on). Switch back to Dictate, or untick *Voice commands*. |
| Permission is on but it doesn't work | After a rebuild macOS treats the app as new. Run `tccutil reset ListenEvent nl.airquote.app` (and the same with `Accessibility` and `Microphone`), restart, and allow again. |
| A transcript says the speaker model needs a login | See *Speaker separation* above. |
| `PaErrorCode -9986` in the log | The audio device changed (AirPods). AirQuote re-reads the devices and retries once. |
| `PermissionError: Operation not permitted` | The folder is in Documents, Desktop, Downloads or cloud storage. Move it and run `./install.sh` again. |

Rebuilding the app (`install.sh` or `scripts/build_app.sh`) is only needed when the build
script itself changes. Each rebuild gives the app a new identity, so the permissions have
to be given again. Changes to the Python code only need a restart of AirQuote.

## Known issues

- **The menu bar icon does not appear on macOS 26.** The window, Dock icon and floating bar
  work. Details in [docs/ARCHITECTUUR.md](docs/ARCHITECTUUR.md).
- **The orange microphone dot stays on for up to 30 seconds after a dictation.** The
  microphone stream is kept open briefly between dictations, because stopping and
  restarting it in quick succession (as a double-tap does) could deadlock the audio driver
  and freeze the app. That deadlock is very likely also the occasional hang noted in
  earlier versions. Nothing is recorded while you are not dictating.
- **Parakeet** reloads its model for every dictation and is slower than it needs to be.

## Development

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
```
```bash
.venv/bin/python -m pytest -q
```

The tests use no real microphone, models or network. Transcribe a file from the terminal
with `scripts/praattypen transcribe recording.m4a`, and speed-test the installed models with
`scripts/praattypen benchmark`. The code is in [praattypen/](praattypen/).

## Credits

AirQuote is built on **[PraatTypen](https://github.com/sebastiaanvankemenade-commits/localwhisper)**
by **Sebastiaan van Kemenade**. His original commits are kept, under his name, in this
repository's history.

**From PraatTypen** — the idea and the foundation:

- local push-to-talk dictation with Whisper on Apple's MLX, entirely on the Mac;
- the app-bundle approach that gives the app its own macOS permissions, instead of
  borrowing Terminal's;
- pasting at the cursor with the clipboard restored afterwards;
- dictation history, the status window and hang detection;
- the research and measurements behind the model choices, and the documents in
  [docs/](docs/).

**Added in AirQuote** (Rob Simons):

- the English redesign: floating bar, window, icon;
- hands-free mode, the Start Dictation button and sound cues;
- Dutch + English detection, custom vocabulary, and the translate, feedback and
  rewrite modes;
- the Recordings tab with speaker separation;
- the Models tab with recommendations and a speed test;
- `install.sh` and portable defaults;
- fixes, including the audio deadlock that froze the app (very likely the hang noted in
  PraatTypen's own README).

Both PraatTypen and AirQuote were developed with [Claude Code](https://claude.com/claude-code).

Speech recognition: [Whisper](https://github.com/openai/whisper) by OpenAI, via
[mlx-whisper](https://github.com/ml-explore/mlx-examples). Speaker separation:
[pyannote.audio](https://github.com/pyannote/pyannote-audio). See [NOTICE](NOTICE).

## License

None yet: the original project has no license file, so all rights to the original work
remain with its author. Do not redistribute without his permission.
