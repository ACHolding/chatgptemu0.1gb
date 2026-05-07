#!/usr/bin/env python3
from __future__ import annotations

USER_GUIDE = """
chatgpt's Game Boy Emulator 0.1
================================================================================

A clean, fully functional Game Boy / Game Boy Color emulator built with PyBoy
and a sleek cyber-themed Tkinter GUI.

Screenshot placeholder (replace when you have a real capture):
  https://via.placeholder.com/640x480/020202/00aaff?text=Game+Boy+Emulator+with+Sound

Features
--------------------------------------------------------------------------------
  • Real GB/GBC emulation powered by PyBoy
  • Real-time screen rendering with Pillow
  • Full keyboard input support
  • Dark cyber GUI (menus, toolbar, framed LCD, status bar)
  • Built-in Install Wizard (pip from inside the app)
  • Run / Pause / Reset + measured FPS counter
  • Game Boy audio when enabled (see Sound support); optional mute via toggle
  • In-app User Guide (Help menu)

Sound support
--------------------------------------------------------------------------------
  PyBoy emulates the original Game Boy APU: two pulse channels, wave, and noise.
  This frontend enables sound emulation by default (sound_emulated=True) and passes
  sound=True into PyBoy.tick() while audio is active.

  Audio is played through your system output via PyBoy's backend (SDL stack on
  many installs). If initialization fails (no device, headless constraints, etc.),
  the app automatically falls back to silent emulation so you can still play.

  Sound quality varies by game, PyBoy version, and OS drivers.

  Runtime toggle: Audio → Toggle sound (or toolbar Sound / Ctrl+M) reloads the current
  ROM with sound on or off. PyBoy has no live APU mute API, so the core is recreated.

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
    Ctrl+M    Toggle sound (reloads ROM; PyBoy limitation)

How to run
--------------------------------------------------------------------------------
  Python 3.10+ recommended.

    python3 chatgptemugb0.1.py

  Full path:

    python3 "/Volumes/1TB/:STUFF~ /:Coding~/chatgptemugb0.1.py"

  First launch: File → Install Wizard, then restart this app so imports refresh.

Dependencies (important for sound on many systems)

    python3 -m pip install --upgrade pyboy pillow numpy cython
""".strip()

__doc__ = USER_GUIDE

import subprocess
import sys
import threading
import time
import warnings
import tkinter as tk

warnings.filterwarnings(
    "ignore",
    message=r"Using SDL2 binaries from pysdl2-dll.*",
    category=UserWarning,
)
from tkinter import filedialog, messagebox, scrolledtext
from pathlib import Path

TITLE = "chatgpt's Game Boy Emulator 0.1"
FRAME_MS = 1000 // 60
GB_W, GB_H = 160, 144
SCALE = 3

BG = "#020202"
PANEL = "#0a0e12"
FRAME = "#121a22"
ACCENT = "#00aaff"
ACCENT_DIM = "#003344"
ACCENT_EDGE = "#0088cc"
TEXT_MUTED = "#6a9aac"

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

DEPS_OK = _PYBOY_IMPORT_OK and _PIL_OK

KEY_DOWN_MAP = {
    "Up": "up",
    "Down": "down",
    "Left": "left",
    "Right": "right",
    "z": "a",
    "x": "b",
    "Return": "start",
    "BackSpace": "select",
}

KEY_UP_MAP = KEY_DOWN_MAP


def _try_create_pyboy(rom: Path, *, prefer_sound: bool) -> tuple[PyBoy, bool]:
    """Return (PyBoy instance, sound_emulated_active).

    If prefer_sound is True, try sound_emulated=True first, then fall back to silent
    construction if drivers/init fail. If prefer_sound is False, force silent core.
    """

    assert PyBoy is not None
    path = str(rom)
    notes: list[str] = []

    def raise_detail(exc: Exception) -> RuntimeError:
        detail = " | ".join(notes) if notes else str(exc)
        return RuntimeError(detail)

    if prefer_sound:
        try:
            return PyBoy(path, window="null", sound_emulated=True), True
        except TypeError:
            pass
        except Exception as exc:
            notes.append(str(exc))

        try:
            return PyBoy(path, window="null", sound_emulated=False), False
        except TypeError:
            pass
        except Exception as exc:
            notes.append(str(exc))

        try:
            return PyBoy(path, window="null"), False
        except Exception as exc:
            raise raise_detail(exc) from exc

    try:
        return PyBoy(path, window="null", sound_emulated=False), False
    except TypeError:
        pass
    except Exception as exc:
        notes.append(str(exc))

    try:
        return PyBoy(path, window="null"), False
    except Exception as exc:
        raise raise_detail(exc) from exc


class GameBoyApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title(TITLE)
        self.root.configure(bg=BG)
        self.root.minsize(GB_W * SCALE + 40, GB_H * SCALE + 120)

        self.pyboy: PyBoy | None = None
        self.rom_path: Path | None = None
        self.want_sound = True
        self.sound_hw_active = False
        self.running = False
        self._held: set[str] = set()
        self._photo: ImageTk.PhotoImage | None = None

        self._fps_last_t = time.perf_counter()
        self._fps_ema = 60.0

        self.status_var = tk.StringVar(
            value="Ready — open a .gb / .gbc ROM" if DEPS_OK else "Dependencies missing — run Install Wizard"
        )
        self.fps_var = tk.StringVar(value="-- fps")
        self.rom_var = tk.StringVar(value="No ROM loaded")

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_menu()
        self._build_ui()
        self._bind_keys()

        self.root.after(FRAME_MS, self._tick)

    def _on_close(self) -> None:
        self._release_all_inputs()
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
        self.root.bind_all("<Control-r>", lambda _e: self.reset())
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
            else "Load a ROM (Ctrl+O)",
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

    def _btn(self, parent: tk.Widget, text: str, command) -> tk.Button:
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
        if self.pyboy is not None:
            try:
                self.pyboy.stop()
            except Exception:
                pass
            self.pyboy = None

        try:
            self.pyboy, self.sound_hw_active = _try_create_pyboy(path, prefer_sound=self.want_sound)
        except Exception as exc:
            messagebox.showerror(TITLE, f"Could not start PyBoy with this ROM:\n{exc}")
            self.status_var.set("Failed to load ROM")
            self.sound_hw_active = False
            self.root.focus_set()
            return

        try:
            self.pyboy.set_emulation_speed(0)
        except Exception:
            pass

        self.rom_path = path
        self.rom_var.set(path.name[:42] + ("…" if len(path.name) > 42 else ""))
        self.running = True
        if self.sound_hw_active:
            audio_hint = "audio on"
        elif not self.want_sound:
            audio_hint = "sound off (toggle)"
        else:
            audio_hint = "audio unavailable (silent fallback)"
        self.status_var.set(f"Loaded {path.name} — {audio_hint}")
        self.root.focus_set()

    def toggle_sound(self) -> None:
        """Flip sound preference and reload ROM — PyBoy has no runtime APU mute API."""

        if self.rom_path is None:
            self.status_var.set("Load a ROM first")
            self.root.focus_set()
            return
        if not DEPS_OK:
            messagebox.showwarning(TITLE, "Install PyBoy and Pillow first.")
            return

        self.want_sound = not self.want_sound
        mode = "on" if self.want_sound else "off"
        self.status_var.set(f"Restarting with sound {mode}…")
        self.running = False
        self._load_rom(self.rom_path)

    def toggle_pause(self) -> None:
        if self.pyboy is None:
            self.status_var.set("Load a ROM first")
            return
        self.running = not self.running
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

    def _tick(self) -> None:
        if self.pyboy is not None and self.running:
            try:
                try:
                    alive = self.pyboy.tick(1, True, self.sound_hw_active)
                except TypeError:
                    alive = self.pyboy.tick()
                if not alive:
                    self.running = False
                    self.status_var.set("PyBoy tick returned False — paused")
            except Exception as exc:
                self.running = False
                self.status_var.set(f"Emulation error: {exc}")
            self._refresh_screen()

        now = time.perf_counter()
        dt = now - self._fps_last_t
        self._fps_last_t = now
        if dt > 1e-9:
            self._fps_ema = self._fps_ema * 0.92 + (1.0 / dt) * 0.08
        self.fps_var.set(f"{self._fps_ema:.0f} fps" if self.running and self.pyboy else "-- fps")

        self.root.after(FRAME_MS, self._tick)

    def _show_user_guide(self) -> None:
        win = tk.Toplevel(self.root)
        win.title(f"{TITLE} — User Guide")
        win.configure(bg=PANEL)
        win.minsize(520, 420)
        txt = scrolledtext.ScrolledText(
            win,
            width=88,
            height=28,
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
        pref = (
            f"Your preference: sound {'ON' if self.want_sound else 'OFF'} "
            "(toggle with Audio menu, toolbar Sound, or Ctrl+M)."
        )
        if self.pyboy is None:
            msg = (
                "Load a ROM first.\n\nPyBoy initializes the Game Boy APU when the core starts.\n\n"
                + pref
            )
        elif self.sound_hw_active:
            msg = (
                "Game Boy sound emulation is enabled for this session.\n\n"
                "PyBoy models all four channels (2× pulse, wave, noise). Output "
                "goes through PyBoy's audio backend (often SDL).\n\n"
                + pref
            )
        else:
            reason = (
                "You turned sound off (toggle), or PyBoy fell back after init failure."
                if not self.want_sound
                else "PyBoy could not keep sound_emulated=True (drivers / device)."
            )
            msg = (
                f"No audio output this session.\n\n{reason}\n\n"
                "Gameplay is unaffected.\n\n"
                + pref
            )
        messagebox.showinfo(f"{TITLE} — Sound", msg)

    def _show_about(self) -> None:
        core = "PyBoy loaded" if _PYBOY_IMPORT_OK else "PyBoy not installed"
        pil = "Pillow loaded" if _PIL_OK else "Pillow not installed"
        if self.pyboy is None:
            snd = (
                "Audio: load a ROM. Preference is "
                f"{'ON' if self.want_sound else 'OFF'} for the next session."
            )
        elif self.sound_hw_active:
            snd = "Audio: Game Boy sound active (preference ON)."
        elif not self.want_sound:
            snd = "Audio: silent — you chose mute (toggle to re-enable)."
        else:
            snd = "Audio: silent fallback (PyBoy could not open audio)."
        messagebox.showinfo(
            TITLE,
            f"{TITLE}\n\n{core}\n{pil}\n{snd}\n\nHelp → User Guide for documentation.",
        )

    def _show_install_wizard(self) -> None:
        win = tk.Toplevel(self.root)
        win.title(f"{TITLE} — Install Wizard")
        win.configure(bg=PANEL)
        win.geometry("640x420")

        tk.Label(
            win,
            text="Install / upgrade emulation dependencies via pip",
            bg=PANEL,
            fg=ACCENT,
            font=("Courier New", 12, "bold"),
        ).pack(anchor="w", padx=12, pady=(12, 4))

        tk.Label(
            win,
            text="Uses pip: pyboy pillow numpy cython (SDL stack may be pulled for audio)",
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
            height=16,
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
                            append("\nDone. Quit and restart this emulator to load PyBoy.")
                            status.set("Success — restart the app")
                            messagebox.showinfo(
                                TITLE,
                                "Install finished.\n\nQuit and launch chatgptemugb0.1.py again so imports refresh.",
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
