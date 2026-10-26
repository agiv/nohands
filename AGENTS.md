# AGENTS.md

Instructions for AI agents working in this repository. How to use the program
is in README.md; the work plan is in `docs/TODO.md`.

## What this is

nohands controls the computer by voice and head movement, without root, on
Ubuntu 26.04 / GNOME 50 / Wayland. Everything runs locally: audio is never
written to disk, all models run on the machine.

    mic ────┬─► vosk (per-mode grammar + arbiter) ─► uinput ─► keys, mouse
            └─► whisper medium on GPU             ─► xclip ─► Ctrl+V
    camera ───► mediapipe face landmarker         ─► uinput ─► cursor

Modes: СОН (sleep) → КОМАНДЫ (commands) ↔ ДИКТОВКА (dictation),
КОМАНДЫ ↔ КОНСОЛЬ (shell), КОМАНДЫ ↔ СЕТКА (cursor to a numbered cell).
Switched by voice or from the tray icon menu.

## Layout

| File | Contents |
|---|---|
| `voiced.py` | daemon: modes, phrase tables (`COMMANDS`, `SHELL_KEYS`, `REPEATABLE`), `Ears` barriers, `Mouse`, `Head` (camera), `Paster`, `Daemon` |
| `shell.py` | КОНСОЛЬ: phrase → shell command (rules, file names, LLM via ollama). Knows nothing about the daemon or audio |
| `grid.py` | СЕТКА: numbers 00–99 → cell centre (pure functions), the GTK4 overlay window as a separate process (`python grid.py`; an X11 override-redirect window via XWayland — it takes no focus and stays above fullscreen apps), `Overlay` — the daemon's end of its stdin/stdout protocol |
| `complete.py` | what a command expects at the next position (program, subcommand, file, directory, options): asks bash-completion like Tab does, options from man or `--help`. Knows nothing about the daemon |
| `tray.py` | tray icon (libayatana-appindicator, its own GTK thread) |
| `speech.py` | Piper voice: `say()` out loud, `synth()` into memory for tests |
| `test_commands.py` | vosk barriers on synthesized speech |
| `test_shell.py` | КОНСОЛЬ rules, no audio |
| `test_grid.py` | СЕТКА: numbers, geometry, `Input.put`, the mode on a fake daemon; `--live` opens the real overlay ten times and moves the real cursor (voice prompts via Piper) |
| `test_mouse.py` | `Mouse` rewind after «стоп» on a fake input, vosk partial-vs-final timing |
| `measure_voice.py` | voice level measurement for the noise gate |
| `bootstrap.sh` | install: apt check, venv, models, service |
| `deploy/` | default `config.toml`, systemd `--user` unit, `nohands` command |
| `docs/superpowers/` | spec and plan of the КОНСОЛЬ mode |

Early experiments (`spike/`, head-tracking findings) are kept on the `spike`
branch, not on develop.

Code lives wherever the repository is cloned (`bootstrap.sh` takes the path
from its own location); venv, models and logs in
`~/.local/share/nohands`; user settings in `~/.config/nohands/config.toml`
(edit that one, not `deploy/config.toml`).

## Running and checks

    PY=~/.local/share/nohands/venv/bin/python
    $PY test_shell.py          # or: $PY -m pytest test_shell.py
    $PY test_commands.py       # loads vosk and Piper, takes ~2 minutes
    nohands restart            # apply changes to voiced.py / shell.py
    nohands log                # what is being recognized right now

Both tests print `ИТОГ: ПРОЙДЕН` on success. Live voice checks (microphone,
camera) are done by the user, not the agent; prompts to the user during such a
check go by voice through `speech.say`, not as text in the terminal.

## Pitfalls

- **Never play test audio through the speakers.** The daemon listens to the
  microphone and will execute the phrase as a real command. Tests synthesize
  speech into memory (`speech.synth`).
- **Every word must be in the vosk dictionary.** The grammar silently drops
  unknown words and the command never fires. On startup the log shows
  `ВНИМАНИЕ: нет в словаре vosk`. `norm()` replaces «ё» with «е», so words
  spelled with «ё» in the dictionary («вперёд») cannot be used.
- **The agent does not name voice commands.** The user picks the phrase; the
  agent checks it against the vosk dictionary and suggests alternatives only
  if a word fails.
- **Destructive commands are stricter.** «стереть», «удалить», «вырезать»,
  «отменить», «закрыть окно» go through `DESTRUCTIVE` (`MIN_CONF`). «ввод»,
  «нет», «контрол цэ» in КОНСОЛЬ go through `STRICT`: a separate phrase, no
  extra words. Any new command that changes the terminal line or destroys
  work belongs there too.
- **The grammar grows multiplicatively.** Repeats («… N раз») add
  (commands × numbers) phrases, and every new phrase is another decoy for
  false positives. Run `test_commands.py` after editing the tables.
- **The log is private.** Spoken and pasted text is logged only with
  `[log] text = true`; otherwise only its length (`shell.shown`). Pass any
  new `log()` of user text through `shell.shown`.
- **No real names from this machine in the repository.** Log cases often
  involve the user's own files, folders, projects and clients. When one goes
  into a test, a comment or a commit message, invent a stand-in of the same
  shape (`atlasmedia/fusion` for the real project path,
  `create.riverport.sh` for a customer script) and re-measure the match
  ratios on the stand-in so the test still reproduces the original failure.
  The same goes for the phrases whisper produced: they echo the real name,
  so they need to be replaced with misspellings of the stand-in.
- **Checks run in a test folder, never in the user's real directories.**
  This includes the agent's own one-off checks, not only `test_shell.py`:
  build a folder with exactly the files and subfolders the case needs
  (`sandbox()` in `test_shell.py` creates them under one per-run root that is
  removed at exit) and point `cwd` / `S.HOME` there. Reproducing a log case
  means rebuilding its layout there with stand-in names, not running rules
  against `~/Projects/...`.
- **A failure must not kill the daemon.** Exceptions in the main loop are
  caught by `Daemon.fail()`; do not swallow them earlier without logging.
- **Do not hardcode the camera position.** The camera may sit off to the
  side, which makes the head-turn range asymmetric. Neutral is taken from the
  current pose; never state a specific camera-to-face angle in code or docs.

## Writing style

- The project language is Russian: code comments, log lines, README, commit
  messages. AGENTS.md is the exception and is kept in English.
- Comments explain *why* and what is wrong with the obvious alternative,
  often with a measurement («"вниз" — 2.12 с против 2.00»). What the code
  does should be visible from the code.
- Commit subject: «Область: что изменилось» (`Консоль: …`, `bootstrap: …`);
  the body says why, as a list if there are several points.
- Temporary and experimental code: do the minimum that works, no polishing.
- Remove a finished item from `docs/TODO.md` in the same commit.
