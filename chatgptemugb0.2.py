#!/usr/bin/env python3
from __future__ import annotations

USER_GUIDE = """
chatgpt's Game Boy Emulator 0.2
================================================================================

A clean Game Boy / Game Boy Color frontend powered by PyBoy with a cyber-themed
Tkinter GUI, real-time Pillow rendering, optional audio playback, and volatile
save handling by default.

0.2 changes
--------------------------------------------------------------------------------
  • Version bumped to chatgptemugb0.2
  • Fixed/kept imports as Python-valid modules: math, tkinter, PyBoy
  • Audio preference is OFF by default, then toggleable from the GUI
  • Optional speaker playback from PyBoy's per-frame sound buffer via pygame
  • PyBoy sound emulation uses sound_emulated / sound_volume / sound_sample_rate
    when supported, with compatibility fallback for older PyBoy versions
  • Persistent RAM/RTC sidecar files are OFF by default: .ram/.rtc save data is
    kept in memory for the current run instead of being written next to the ROM
  • Python bytecode cache output is disabled for this launcher
  • Safer PyBoy.tick compatibility: v2 style tick(1, True), old-style fallback

Sound support
--------------------------------------------------------------------------------
  PyBoy emulates the original Game Boy APU. This frontend starts with sound OFF
  so it is quiet by default. Turn it on with Audio → Toggle sound, the toolbar
  Sound button, or Ctrl+M. Because PyBoy sound emulation is initialized with the
  core, changing sound reloads the current ROM.

  When sound is ON, this app asks PyBoy for a 48 kHz stereo sound buffer each
  frame and sends it to pygame's mixer. If pygame or the audio device cannot be
  initialized, the emulator falls back to silent play.

  Install Wizard includes pygame for audio playback. Gameplay still works without
  pygame; only speaker output is disabled.

Save / sidecar files
--------------------------------------------------------------------------------
  PERSISTENT_SAVE_FILES is False by default. That means the app passes in-memory
  RAM/RTC file objects to PyBoy, avoiding automatic save sidecar files next to the
  ROM. To allow persistent game saves, change PERSISTENT_SAVE_FILES to True.

Keyboard controls
--------------------------------------------------------------------------------
  Game Boy      PC keys
  ----------    ----------------
  D-Pad         Arrow keys
  A             Z
  B             X
  Start         Enter
  Select        BackSpace

  Global hotkeys:
    Esc       Pause / resume
    Ctrl+O    Open ROM
    Ctrl+R    Reset
    Ctrl+Q    Quit
    Ctrl+M    Toggle sound (reloads ROM if one is loaded)

How to run
--------------------------------------------------------------------------------
  Python 3.10+ recommended.

    python3 chatgptemugb0.2.py

  First launch: File → Install Wizard, then restart this app so imports refresh.

Dependencies
--------------------------------------------------------------------------------
    python3 -m pip install --upgrade pyboy pillow numpy pygame cython
""".strip()

__doc__ = USER_GUIDE

import io
import math
import subprocess
import sys
import threading
import time
import warnings
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext

# Keep this single-file launcher tidy: no __pycache__ bytecode output.
sys.dont_write_bytecode = True

warnings.filterwarnings(
    "ignore",
    message=r"Using SDL2 binaries from pysdl2-dll.*",
    category=UserWarning,
)

TITLE = "chatgpt's Game Boy Emulator 0.2"
FRAME_MS = 1000 // 60
GB_W, GB_H = 160, 144
SCALE = 3

# User requested files off: False keeps save RAM/RTC in memory only.
PERSISTENT_SAVE_FILES = False

# Audio is intentionally quiet on launch. Use Ctrl+M / Sound to enable.
DEFAULT_SOUND_ON = False
AUDIO_SAMPLE_RATE = 48_000  # divisible by 60; matches PyBoy constructor rule
AUDIO_VOLUME_PERCENT = 80
AUDIO_PYGAME_BUFFER = 512

BG = "#020202"
PANEL = "#0a0e12"
FRAME = "#121a22"
ACCENT = "#00aaff"
ACCENT_DIM = "#003344"
ACCENT_EDGE = "#0088cc"
TEXT_MUTED = "#6a9aac"
WARN = "#ffcc66"

try:
    from pyboy import PyBoy

    _PYBOY_IMPORT_OK = True
except ImportError:
    PyBoy = None  # type: ignore[misc, assignment]
    _PYBOY_IMPORT_OK = False

try:
    from PIL import Image, ImageTk

    _PIL_OK = True
except ImportError:
    Image = ImageTk = None  # type: ignore[misc, assignment]
    _PIL_OK = False

try:
    import numpy as np

    _NUMPY_OK = True
except ImportError:
    np = None  # type: ignore[assignment]
    _NUMPY_OK = False

try:
    import pygame

    _PYGAME_OK = True
except ImportError:
    pygame = None  # type: ignore[assignment]
    _PYGAME_OK = False

DEPS_OK = _PYBOY_IMPORT_OK and _PIL_OK
AUDIO_DEPS_OK = _NUMPY_OK and _PYGAME_OK

KEY_DOWN_MAP = {
    "Up": "up",
    "Down": "down",
    "Left": "left",
    "Right": "right",
    "z": "a",
    "Z": "a",
    "x": "b",
    "X": "b",
    "Return": "start",
    "BackSpace": "select",
}

KEY_UP_MAP = KEY_DOWN_MAP


def _volatile_file() -> io.BytesIO:
    """A fresh file-like object so PyBoy does not create sidecar save files."""

    return io.BytesIO()


def _base_pyboy_kwargs() -> dict[str, object]:
    kwargs: dict[str, object] = {"window": "null"}
    if not PERSISTENT_SAVE_FILES:
        kwargs["ram_file"] = _volatile_file()
        kwargs["rtc_file"] = _volatile_file()
    return kwargs


def _candidate_kwargs(*, prefer_sound: bool) -> list[tuple[dict[str, object], bool]]:
    """Return constructor kwargs and whether they should enable sound emulation."""

    candidates: list[tuple[dict[str, object], bool]] = []

    def fresh(extra: dict[str, object], sound_active: bool) -> tuple[dict[str, object], bool]:
        kwargs = _base_pyboy_kwargs()
        kwargs.update(extra)
        return kwargs, sound_active

    if prefer_sound:
        # Current PyBoy API.
        candidates.append(
            fresh(
                {
                    "sound_emulated": True,
                    "sound_volume": AUDIO_VOLUME_PERCENT,
                    "sound_sample_rate": AUDIO_SAMPLE_RATE,
                },
                True,
            )
        )
        # Same API, fewer knobs for minor-version compatibility.
        candidates.append(fresh({"sound_emulated": True, "sound_volume": AUDIO_VOLUME_PERCENT}, True))
        candidates.append(fresh({"sound_emulated": True}, True))
        # Older PyBoy compatibility: deprecated constructor sound flag.
        candidates.append(fresh({"sound": True}, True))

    # Silent core. This is also the default path when sound is off.
    candidates.append(fresh({"sound_emulated": False, "sound_volume": 0}, False))
    candidates.append(fresh({"sound_emulated": False}, False))
    candidates.append(fresh({}, False))
    return candidates


def _try_create_pyboy(rom: Path, *, prefer_sound: bool) -> tuple[PyBoy, bool, str]:
    """Return (PyBoy instance, sound_emulated_active, diagnostic_note)."""

    assert PyBoy is not None
    notes: list[str] = []

    for kwargs, sound_active in _candidate_kwargs(prefer_sound=prefer_sound):
        try:
            return PyBoy(str(rom), **kwargs), sound_active, ""
        except TypeError as exc:
            notes.append(f"unsupported kwargs {sorted(kwargs)}: {exc}")
        except Exception as exc:
            notes.append(str(exc))
            # If sound was requested, keep trying silent fallbacks. If sound was not
            # requested, the first real failure is probably ROM/backend related.
            if not prefer_sound:
                break

    detail = " | ".join(n for n in notes if n) or "unknown PyBoy initialization failure"
    raise RuntimeError(detail)


class PygameAudioEngine:
    """Tiny streaming bridge from PyBoy sound buffers to pygame.mixer."""

    def __init__(self, sample_rate: int, *, volume_percent: int = AUDIO_VOLUME_PERCENT) -> None:
        if pygame is None or np is None:
            raise RuntimeError("pygame and numpy are required for speaker audio")

        self.pygame = pygame
        self.np = np
        self.sample_rate = int(sample_rate) if sample_rate else AUDIO_SAMPLE_RATE
        self.volume_percent = max(0, min(100, int(volume_percent)))
        self._closed = False

        try:
            pygame.mixer.quit()
        except Exception:
            pass

        pygame.mixer.pre_init(
            frequency=self.sample_rate,
            size=-16,
            channels=2,
            buffer=AUDIO_PYGAME_BUFFER,
        )
        pygame.mixer.init(
            frequency=self.sample_rate,
            size=-16,
            channels=2,
            buffer=AUDIO_PYGAME_BUFFER,
        )
        pygame.mixer.set_num_channels(2)
        self.channel = pygame.mixer.Channel(0)
        self.channel.set_volume(self.volume_percent / 100.0)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.channel.stop()
        except Exception:
            pass
        try:
            self.pygame.mixer.quit()
        except Exception:
            pass

    def feed(self, frame_audio) -> None:  # noqa: ANN001 - accepts numpy-like buffers
        if self._closed or frame_audio is None:
            return

        arr = self.np.asarray(frame_audio)
        if arr.size == 0:
            return

        if arr.ndim == 1:
            if arr.size < 2:
                return
            if arr.size % 2:
                arr = arr[:-1]
            arr = arr.reshape(-1, 2)
        elif arr.ndim > 2:
            arr = arr.reshape(-1, arr.shape[-1])

        if arr.shape[-1] != 2:
            # Keep the audio path stereo because PyBoy's documented buffer is stereo.
            return

        if arr.dtype == self.np.int8:
            pcm16 = arr.astype(self.np.int16) << 8
        elif arr.dtype == self.np.uint8:
            pcm16 = (arr.astype(self.np.int16) - 128) << 8
        elif self.np.issubdtype(arr.dtype, self.np.floating):
            pcm16 = (self.np.clip(arr, -1.0, 1.0) * 32767).astype(self.np.int16)
        elif arr.dtype == self.np.int16:
            pcm16 = arr
        else:
            pcm16 = self.np.clip(arr, -32768, 32767).astype(self.np.int16)

        if not pcm16.flags["C_CONTIGUOUS"]:
            pcm16 = self.np.ascontiguousarray(pcm16)

        sound = self.pygame.sndarray.make_sound(pcm16)
        # Allow one queued buffer. Drop extras rather than building latency.
        if not self.channel.get_busy():
            self.channel.play(sound)
        elif self.channel.get_queue() is None:
            self.channel.queue(sound)


class GameBoyApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title(TITLE)
        self.root.configure(bg=BG)
        self.root.minsize(GB_W * SCALE + 40, GB_H * SCALE + 120)

        self.pyboy: PyBoy | None = None
        self.rom_path: Path | None = None
        self.want_sound = DEFAULT_SOUND_ON
        self.sound_emulated_active = False
        self.audio_output_active = False
        self.audio_engine: PygameAudioEngine | None = None
        self.running = False
        self._held: set[str] = set()
        self._photo: ImageTk.PhotoImage | None = None

        self._fps_last_t = time.perf_counter()
        self._fps_ema = 60.0
        self._audio_error_shown = False

        self.status_var = tk.StringVar(
            value="Ready — open a .gb / .gbc ROM" if DEPS_OK else "Dependencies missing — run Install Wizard"
        )
        self.fps_var = tk.StringVar(value="-- fps")
        self.rom_var = tk.StringVar(value="No ROM loaded")
        self.audio_var = tk.StringVar(value=self._audio_label())

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_menu()
        self._build_ui()
        self._bind_keys()

        self.root.after(FRAME_MS, self._tick)

    def _audio_label(self) -> str:
        if not self.want_sound:
            return "Sound: off"
        if self.audio_output_active:
            return "Sound: on"
        if self.sound_emulated_active:
            return "Sound: emulated"
        return "Sound: pending"

    def _refresh_audio_label(self) -> None:
        self.audio_var.set(self._audio_label())

    def _on_close(self) -> None:
        self._release_all_inputs()
        self._stop_audio_engine()
        if self.pyboy is not None:
            try:
                self.pyboy.stop()
            except Exception:
                pass
        self.root.destroy()

    def _build_menu(self) -> None:
        menubar = tk.Menu(
            self.root,
            bg=PANEL,
            fg=ACCENT,
            activebackground=ACCENT_DIM,
            activeforeground=ACCENT,
            borderwidth=0,
        )

        file_m = self._submenu()
        file_m.add_command(label="Open ROM...", accelerator="Ctrl+O", command=self.open_rom)
        file_m.add_command(label="Install Wizard...", command=self._show_install_wizard)
        file_m.add_separator()
        file_m.add_command(label="Quit", accelerator="Ctrl+Q", command=self._on_close)
        menubar.add_cascade(label="File", menu=file_m)

        emu_m = self._submenu()
        emu_m.add_command(label="Run / Pause", accelerator="Esc", command=self.toggle_pause)
        emu_m.add_command(label="Reset", accelerator="Ctrl+R", command=self.reset)
        menubar.add_cascade(label="Emulation", menu=emu_m)

        audio_m = self._submenu()
        audio_m.add_command(
            label="Toggle sound (reload ROM)",
            accelerator="Ctrl+M",
            command=self.toggle_sound,
        )
        audio_m.add_separator()
        audio_m.add_command(label="Sound info…", command=self._show_sound_info)
        menubar.add_cascade(label="Audio", menu=audio_m)

        help_m = self._submenu()
        help_m.add_command(label="User Guide", command=self._show_user_guide)
        help_m.add_command(label="About", command=self._show_about)
        menubar.add_cascade(label="Help", menu=help_m)

        self.root.configure(menu=menubar)

        self.root.bind_all("<Control-o>", lambda _e: self.open_rom())
        self.root.bind_all("<Control-O>", lambda _e: self.open_rom())
        self.root.bind_all("<Control-q>", lambda _e: self._on_close())
        self.root.bind_all("<Control-Q>", lambda _e: self._on_close())
        self.root.bind_all("<Control-r>", lambda _e: self.reset())
        self.root.bind_all("<Control-R>", lambda _e: self.reset())
        self.root.bind_all("<Control-m>", lambda _e: self.toggle_sound())
        self.root.bind_all("<Control-M>", lambda _e: self.toggle_sound())

    def _submenu(self) -> tk.Menu:
        return tk.Menu(
            self.root,
            tearoff=0,
            bg=PANEL,
            fg=ACCENT,
            activebackground=ACCENT_DIM,
            activeforeground=ACCENT,
            borderwidth=0,
        )

    def _build_ui(self) -> None:
        toolbar = tk.Frame(self.root, bg=PANEL)
        toolbar.pack(fill="x", side="top")

        self._btn(toolbar, "Open", self.open_rom).pack(side="left", padx=4, pady=6)
        self._btn(toolbar, "Install", self._show_install_wizard).pack(side="left", padx=4, pady=6)
        self._btn(toolbar, "Run/Pause", self.toggle_pause).pack(side="left", padx=4, pady=6)
        self._btn(toolbar, "Reset", self.reset).pack(side="left", padx=4, pady=6)
        self._btn(toolbar, "Sound", self.toggle_sound).pack(side="left", padx=4, pady=6)

        tk.Label(
            toolbar,
            textvariable=self.audio_var,
            bg=PANEL,
            fg=WARN if DEFAULT_SOUND_ON else TEXT_MUTED,
            font=("Courier New", 9),
            anchor="e",
        ).pack(side="right", padx=(8, 4))

        tk.Label(
            toolbar,
            textvariable=self.rom_var,
            bg=PANEL,
            fg=TEXT_MUTED,
            font=("Courier New", 9),
            anchor="e",
        ).pack(side="right", padx=8)

        viewport = tk.Frame(self.root, bg=FRAME, padx=8, pady=8)
        viewport.pack(fill="both", expand=True, padx=10, pady=(4, 8))

        self.screen_label = tk.Label(
            viewport,
            bg=BG,
            fg=ACCENT,
            text=(
                "PyBoy + Pillow required.\n\n"
                "File → Install Wizard\n"
                "then restart this app."
            )
            if not DEPS_OK
            else "Load a ROM (Ctrl+O)\n\nSound starts off; Ctrl+M toggles it.",
            font=("Courier New", 12),
            justify="center",
            highlightthickness=2,
            highlightbackground=ACCENT_EDGE,
        )
        self.screen_label.pack()

        statusbar = tk.Frame(self.root, bg=PANEL)
        statusbar.pack(fill="x", side="bottom")

        tk.Label(
            statusbar,
            textvariable=self.status_var,
            bg=PANEL,
            fg=ACCENT,
            font=("Courier New", 10),
            anchor="w",
            padx=8,
            pady=5,
        ).pack(side="left", fill="x", expand=True)

        tk.Label(
            statusbar,
            textvariable=self.fps_var,
            bg=PANEL,
            fg=ACCENT,
            font=("Courier New", 10),
            padx=8,
            pady=5,
        ).pack(side="right")

    def _btn(self, parent: tk.Widget, text: str, command) -> tk.Button:  # noqa: ANN001
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=BG,
            fg=ACCENT,
            activebackground=ACCENT_DIM,
            activeforeground=ACCENT,
            highlightbackground=ACCENT_EDGE,
            highlightcolor=ACCENT,
            highlightthickness=1,
            relief="flat",
            font=("Courier New", 10, "bold"),
            padx=12,
            pady=4,
            cursor="hand2",
        )

    def _bind_keys(self) -> None:
        self.root.bind("<KeyPress>", self._on_key_press)
        self.root.bind("<KeyRelease>", self._on_key_release)
        self.root.bind("<FocusOut>", lambda _e: self._release_all_inputs())
        self.root.focus_set()

    def _on_key_press(self, event: tk.Event) -> None:
        keysym = event.keysym
        if keysym == "Escape":
            self.toggle_pause()
            return
        if not self.pyboy or keysym not in KEY_DOWN_MAP:
            return
        name = KEY_DOWN_MAP[keysym]
        if name in self._held:
            return
        try:
            self.pyboy.button_press(name)
            self._held.add(name)
        except Exception:
            pass

    def _on_key_release(self, event: tk.Event) -> None:
        keysym = event.keysym
        if keysym not in KEY_UP_MAP:
            return
        name = KEY_UP_MAP[keysym]
        if name not in self._held:
            return
        try:
            if self.pyboy:
                self.pyboy.button_release(name)
        except Exception:
            pass
        self._held.discard(name)

    def _release_all_inputs(self) -> None:
        if not self.pyboy:
            self._held.clear()
            return
        for name in list(self._held):
            try:
                self.pyboy.button_release(name)
            except Exception:
                pass
        self._held.clear()

    def open_rom(self) -> None:
        if not DEPS_OK:
            messagebox.showwarning(TITLE, "Install PyBoy and Pillow first (File → Install Wizard).")
            return
        path = filedialog.askopenfilename(
            title="Open Game Boy ROM",
            filetypes=(
                ("Game Boy ROMs", "*.gb *.gbc"),
                ("All files", "*.*"),
            ),
        )
        if not path:
            self.root.focus_set()
            return
        self._load_rom(Path(path))

    def _load_rom(self, path: Path) -> None:
        assert PyBoy is not None and Image is not None and ImageTk is not None
        self._release_all_inputs()
        self._stop_audio_engine()
        if self.pyboy is not None:
            try:
                self.pyboy.stop()
            except Exception:
                pass
            self.pyboy = None

        self.sound_emulated_active = False
        self.audio_output_active = False
        self._audio_error_shown = False

        try:
            self.pyboy, self.sound_emulated_active, note = _try_create_pyboy(
                path,
                prefer_sound=self.want_sound,
            )
        except Exception as exc:
            messagebox.showerror(TITLE, f"Could not start PyBoy with this ROM:\n{exc}")
            self.status_var.set("Failed to load ROM")
            self.root.focus_set()
            self._refresh_audio_label()
            return

        try:
            self.pyboy.set_emulation_speed(0)
        except Exception:
            pass

        audio_hint = self._prepare_audio_after_load()

        self.rom_path = path
        self.rom_var.set(path.name[:42] + ("…" if len(path.name) > 42 else ""))
        self.running = True
        save_hint = "save files off" if not PERSISTENT_SAVE_FILES else "persistent saves on"
        if note:
            self.status_var.set(f"Loaded {path.name} — {audio_hint}; {save_hint}; {note[:60]}")
        else:
            self.status_var.set(f"Loaded {path.name} — {audio_hint}; {save_hint}")
        self._refresh_audio_label()
        self.root.focus_set()

    def _prepare_audio_after_load(self) -> str:
        if not self.want_sound:
            return "sound off"
        if not self.sound_emulated_active:
            return "audio unavailable (silent core)"
        if not AUDIO_DEPS_OK:
            return "audio emulated, pygame/numpy missing"
        try:
            sample_rate = AUDIO_SAMPLE_RATE
            try:
                sound_api = getattr(self.pyboy, "sound", None)
                if sound_api is not None:
                    sample_rate = int(getattr(sound_api, "sample_rate", AUDIO_SAMPLE_RATE))
            except Exception:
                pass
            self.audio_engine = PygameAudioEngine(sample_rate, volume_percent=AUDIO_VOLUME_PERCENT)
            self.audio_output_active = True
            return "audio on"
        except Exception as exc:
            self.audio_output_active = False
            self.audio_engine = None
            return f"audio emulated, output unavailable: {exc}"

    def _stop_audio_engine(self) -> None:
        if self.audio_engine is not None:
            try:
                self.audio_engine.close()
            except Exception:
                pass
        self.audio_engine = None
        self.audio_output_active = False
        self._refresh_audio_label() if hasattr(self, "audio_var") else None

    def toggle_sound(self) -> None:
        self.want_sound = not self.want_sound
        mode = "on" if self.want_sound else "off"
        self._refresh_audio_label()

        if self.rom_path is None:
            self.status_var.set(f"Sound preference set to {mode} for the next ROM")
            self.root.focus_set()
            return
        if not DEPS_OK:
            messagebox.showwarning(TITLE, "Install PyBoy and Pillow first.")
            return

        self.status_var.set(f"Restarting with sound {mode}…")
        self.running = False
        self._load_rom(self.rom_path)

    def toggle_pause(self) -> None:
        if self.pyboy is None:
            self.status_var.set("Load a ROM first")
            return
        self.running = not self.running
        if not self.running:
            self._release_all_inputs()
        self.status_var.set("Running" if self.running else "Paused")
        self.root.focus_set()

    def reset(self) -> None:
        if self.rom_path is None:
            self.status_var.set("No ROM to reset")
            return
        if not DEPS_OK:
            return
        self._load_rom(self.rom_path)

    def _refresh_screen(self) -> None:
        if self.pyboy is None or Image is None or ImageTk is None:
            return
        try:
            arr = self.pyboy.screen.ndarray.copy()
            im = Image.fromarray(arr, mode="RGBA")
            im = im.resize((GB_W * SCALE, GB_H * SCALE), Image.Resampling.NEAREST)
            self._photo = ImageTk.PhotoImage(im)
            self.screen_label.configure(image=self._photo, text="")
        except Exception as exc:
            self.status_var.set(f"Display error: {exc}")

    def _feed_audio(self) -> None:
        if self.pyboy is None or self.audio_engine is None or not self.audio_output_active:
            return
        try:
            sound_api = getattr(self.pyboy, "sound", None)
            if sound_api is None:
                return
            self.audio_engine.feed(sound_api.ndarray)
        except Exception as exc:
            self.audio_output_active = False
            self._stop_audio_engine()
            self._refresh_audio_label()
            if not self._audio_error_shown:
                self._audio_error_shown = True
                self.status_var.set(f"Audio stopped: {exc}")

    def _tick_pyboy(self) -> bool:
        assert self.pyboy is not None
        try:
            return bool(self.pyboy.tick(1, True))
        except TypeError:
            return bool(self.pyboy.tick())

    def _tick(self) -> None:
        if self.pyboy is not None and self.running:
            try:
                alive = self._tick_pyboy()
                if not alive:
                    self.running = False
                    self.status_var.set("PyBoy tick returned False — paused")
                else:
                    self._feed_audio()
            except Exception as exc:
                self.running = False
                self._release_all_inputs()
                self.status_var.set(f"Emulation error: {exc}")
            self._refresh_screen()

        now = time.perf_counter()
        dt = now - self._fps_last_t
        self._fps_last_t = now
        if dt > 1e-9:
            instant_fps = 1.0 / dt
            if math.isfinite(instant_fps):
                self._fps_ema = self._fps_ema * 0.92 + instant_fps * 0.08
        self.fps_var.set(f"{self._fps_ema:.0f} fps" if self.running and self.pyboy else "-- fps")

        self.root.after(FRAME_MS, self._tick)

    def _show_user_guide(self) -> None:
        win = tk.Toplevel(self.root)
        win.title(f"{TITLE} — User Guide")
        win.configure(bg=PANEL)
        win.minsize(560, 440)
        txt = scrolledtext.ScrolledText(
            win,
            width=92,
            height=30,
            wrap="word",
            bg=BG,
            fg=ACCENT,
            insertbackground=ACCENT,
            font=("Courier New", 11),
            relief="flat",
            highlightthickness=1,
            highlightbackground=ACCENT_EDGE,
            padx=10,
            pady=10,
        )
        txt.pack(fill="both", expand=True, padx=10, pady=(10, 0))
        txt.insert("1.0", USER_GUIDE)
        txt.configure(state="disabled")
        bar = tk.Frame(win, bg=PANEL)
        bar.pack(fill="x", pady=(6, 10))
        self._btn(bar, "Close", win.destroy).pack(side="right", padx=10)

    def _show_sound_info(self) -> None:
        pref = f"Your preference: sound {'ON' if self.want_sound else 'OFF'} (Ctrl+M toggles it)."
        deps = []
        deps.append("PyBoy loaded" if _PYBOY_IMPORT_OK else "PyBoy missing")
        deps.append("Pillow loaded" if _PIL_OK else "Pillow missing")
        deps.append("NumPy loaded" if _NUMPY_OK else "NumPy missing")
        deps.append("pygame loaded" if _PYGAME_OK else "pygame missing")

        if self.pyboy is None:
            msg = "Load a ROM first.\n\n" + pref
        elif self.audio_output_active:
            msg = "Speaker audio is active through pygame.mixer.\n\n" + pref
        elif self.sound_emulated_active:
            msg = "PyBoy sound emulation is active, but speaker output is not active.\n\n" + pref
        elif self.want_sound:
            msg = "Sound was requested, but this session fell back to silent emulation.\n\n" + pref
        else:
            msg = "Sound is off for this session.\n\n" + pref

        msg += "\n\nDependencies:\n  " + "\n  ".join(deps)
        msg += "\n\nInstall Wizard can install pyboy, pillow, numpy, pygame, and cython."
        messagebox.showinfo(f"{TITLE} — Sound", msg)

    def _show_about(self) -> None:
        core = "PyBoy loaded" if _PYBOY_IMPORT_OK else "PyBoy not installed"
        pil = "Pillow loaded" if _PIL_OK else "Pillow not installed"
        np_status = "NumPy loaded" if _NUMPY_OK else "NumPy not installed"
        pg = "pygame loaded" if _PYGAME_OK else "pygame not installed"
        save_status = "Persistent save sidecar files: OFF" if not PERSISTENT_SAVE_FILES else "Persistent saves: ON"
        audio = self._audio_label()
        messagebox.showinfo(
            TITLE,
            f"{TITLE}\n\n{core}\n{pil}\n{np_status}\n{pg}\n{audio}\n{save_status}\n\nHelp → User Guide for documentation.",
        )

    def _show_install_wizard(self) -> None:
        win = tk.Toplevel(self.root)
        win.title(f"{TITLE} — Install Wizard")
        win.configure(bg=PANEL)
        win.geometry("700x450")

        tk.Label(
            win,
            text="Install / upgrade emulation dependencies via pip",
            bg=PANEL,
            fg=ACCENT,
            font=("Courier New", 12, "bold"),
        ).pack(anchor="w", padx=12, pady=(12, 4))

        tk.Label(
            win,
            text="Uses pip: pyboy pillow numpy pygame cython",
            bg=PANEL,
            fg=TEXT_MUTED,
            font=("Courier New", 10),
        ).pack(anchor="w", padx=12)

        log = scrolledtext.ScrolledText(
            win,
            wrap="word",
            bg=BG,
            fg=ACCENT,
            font=("Courier New", 9),
            height=17,
            relief="flat",
            highlightthickness=1,
            highlightbackground=ACCENT_EDGE,
        )
        log.pack(fill="both", expand=True, padx=12, pady=10)

        status = tk.StringVar(value="Idle — click Install to begin")

        cmd = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "pyboy",
            "pillow",
            "numpy",
            "pygame",
            "cython",
        ]

        def append(msg: str) -> None:
            log.configure(state="normal")
            log.insert("end", msg + "\n")
            log.see("end")
            log.configure(state="disabled")

        bar = tk.Frame(win, bg=PANEL)
        bar.pack(fill="x", pady=(0, 12))
        install_btn = self._btn(bar, "Install / upgrade", lambda: None)
        install_btn.pack(side="left", padx=12)
        self._btn(bar, "Close", win.destroy).pack(side="right", padx=12)
        tk.Label(bar, textvariable=status, bg=PANEL, fg=TEXT_MUTED, font=("Courier New", 9)).pack(
            side="left", padx=12
        )

        def run_install() -> None:
            install_btn.configure(state="disabled")
            status.set("Running pip …")
            append("$ " + " ".join(cmd))

            def worker() -> None:
                try:
                    proc = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=600,
                    )
                    out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
                    code = proc.returncode

                    def done() -> None:
                        append(out.strip() or "(no output)")
                        if code == 0:
                            append("\nDone. Quit and restart this emulator to refresh imports.")
                            status.set("Success — restart the app")
                            messagebox.showinfo(
                                TITLE,
                                "Install finished.\n\nQuit and launch chatgptemugb0.2.py again so imports refresh.",
                            )
                        else:
                            status.set(f"pip exited with {code}")
                            messagebox.showerror(TITLE, f"pip failed (exit {code}). See log in wizard.")
                        install_btn.configure(state="normal")

                    self.root.after(0, done)
                except Exception as exc:

                    def fail_report(e: BaseException = exc) -> None:
                        append(str(e))
                        status.set("Failed")
                        install_btn.configure(state="normal")
                        messagebox.showerror(TITLE, str(e))

                    self.root.after(0, fail_report)

            threading.Thread(target=worker, daemon=True).start()

        install_btn.configure(command=run_install)


def main() -> None:
    GameBoyApp().root.mainloop()


if __name__ == "__main__":
    main()
