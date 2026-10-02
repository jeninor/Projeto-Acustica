#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
batch_capture_balloon_spectra_v1.py

Captura por lotes del panel Balloon-spectra de CLF Viewer.

Objetivo
--------
- Abrir cada CF2 de forma transaccional en la MISMA ventana del viewer.
- Activar la vista Balloon-spectra.
- Barrer sistemáticamente dos barras de desplazamiento (horizontal/vertical)
  que controlan los ángulos.
- Guardar imágenes del panel Balloon-spectra para cada par angular.
- Guardar además un pequeño recorte de la zona donde el viewer muestra los
  ángulos, para auditoría/OCR posterior.
- Soportar --resume y reanudación/reparación: si un modelo ya tiene todas las
  imágenes esperadas, se salta; si sólo tiene una parte, continúa y genera
  únicamente las faltantes.

Notas
-----
- Esta V1 está optimizada para CAPTURAR IMÁGENES, no para extraer valores.
- Los ángulos guardados en manifest.csv son los ángulos SOLICITADOS, calculados
  a partir de una malla configurable (h-start, h-step, v-start, v-step).
- Como red de seguridad, se guarda angle_label.png con la lectura visual del viewer.
- Si después confirmamos que la malla coincide perfectamente con el viewer,
  podremos pasar a extracción numérica offline.

Requisitos:
    pip install pywinauto pillow pywin32
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import ctypes
import io
import zipfile
import json
import os
import re
import shutil
import socket
import threading
import time
from ctypes import wintypes
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageGrab
from pywinauto import Application, Desktop, keyboard, mouse

import win32con
import win32gui
import win32ui


# ----------------------------------------------------------------------
# Helpers generales
# ----------------------------------------------------------------------

def safe_model_name(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", path.stem)


def click_control(ctrl):
    try:
        ctrl.click_input()
        return
    except Exception:
        pass

    try:
        ctrl.set_focus()
    except Exception:
        pass

    try:
        ctrl.invoke()
        return
    except Exception:
        pass

    raise RuntimeError(f"No pude hacer click en: {ctrl}")


def capture_window(win) -> Image.Image:
    return win.capture_as_image()


def absolute_bbox_from_ratios(win, ratios: Tuple[float, float, float, float]):
    r = win.rectangle()
    x0r, y0r, x1r, y1r = ratios
    w = int(r.width())
    h = int(r.height())
    return (
        int(round(r.left + w * x0r)),
        int(round(r.top + h * y0r)),
        int(round(r.left + w * x1r)),
        int(round(r.top + h * y1r)),
    )


def capture_screen_bbox_fast(win, bbox, fallback_ratios=None) -> Image.Image:
    """Capture only the requested screen rectangle. Much cheaper than capture_as_image()."""
    try:
        try:
            return ImageGrab.grab(bbox=bbox, all_screens=True)
        except TypeError:
            return ImageGrab.grab(bbox=bbox)
    except Exception:
        if fallback_ratios is None:
            raise
        return crop_by_ratios(capture_window(win), fallback_ratios)


def crop_by_ratios(image: Image.Image, ratios: Tuple[float, float, float, float]) -> Image.Image:
    x0r, y0r, x1r, y1r = ratios
    w, h = image.size
    x0 = max(0, min(w, int(round(w * x0r))))
    y0 = max(0, min(h, int(round(h * y0r))))
    x1 = max(0, min(w, int(round(w * x1r))))
    y1 = max(0, min(h, int(round(h * y1r))))
    if x1 <= x0 or y1 <= y0:
        raise RuntimeError(f"Recorte inválido: {ratios} para imagen {image.size}")
    return image.crop((x0, y0, x1, y1))


def save_png_fast(image: Image.Image, path: Path, compress_level: int):
    """Crash-safe PNG write: never expose a half-written final PNG."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".part.{os.getpid()}.{threading.get_ident()}")
    try:
        image.save(str(tmp), format="PNG", compress_level=compress_level, optimize=False)
        os.replace(str(tmp), str(path))
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass





def encode_png_raw_bytes(
    raw_bgrx: bytes,
    width: int,
    height: int,
    compress_level: int,
) -> bytes:
    """Encode immutable BGRX bytes to PNG bytes inside a worker."""
    image = Image.frombuffer(
        "RGB",
        (int(width), int(height)),
        raw_bgrx,
        "raw",
        "BGRX",
        0,
        1,
    ).copy()

    bio = io.BytesIO()
    image.save(
        bio,
        format="PNG",
        compress_level=int(compress_level),
        optimize=False,
    )
    return bio.getvalue()


def save_png_raw_fast(
    raw_bgrx: bytes,
    width: int,
    height: int,
    path: Path,
    compress_level: int,
):
    """
    Reconstruct PIL and encode PNG entirely inside a save worker.

    The sweep thread performs only BitBlt + GetBitmapBits and queues immutable
    BGRX bytes. Exact pixel equivalence was validated before integration.
    """
    image = Image.frombuffer(
        "RGB",
        (
            int(width),
            int(height),
        ),
        raw_bgrx,
        "raw",
        "BGRX",
        0,
        1,
    ).copy()

    save_png_fast(
        image,
        path,
        compress_level,
    )


def _normalize_cf2_identity(value: str) -> str:
    s = str(value or "").strip().lower().replace("\\", "/")
    s = s.rsplit("/", 1)[-1]
    if s.endswith(".cf2"):
        s = s[:-4]
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def loaded_title_matches_cf2(title: str, path: Path) -> bool:
    title_id = _normalize_cf2_identity(title)
    req_id = _normalize_cf2_identity(path.stem)
    return bool(req_id and req_id in title_id)


def _v6103_requested_identity_tokens(path: Path):
    """
    Identity tokens for local-cache CF2 files.

    Local cache filenames are:
        <stem up to 70 chars>__<10-char SHA1>.CF2

    The unique digest suffix is especially useful because CLF Viewer can
    visually/truly shorten the middle of long filenames in its title bar,
    while the status bar still exposes the loaded cache path.
    """
    stem = str(Path(path).stem)

    normalized_full = _normalize_cf2_identity(stem)

    digest = ""

    m = re.search(
        r"__([0-9a-fA-F]{10})$",
        stem,
    )

    if m:
        digest = m.group(1).lower()

    basename = Path(path).name.lower()

    return {
        "normalized_full": normalized_full,
        "digest": digest,
        "basename": basename,
    }


def _v6103_native_child_text_records(root_hwnd):
    rows = []

    def callback(hwnd, _):
        try:
            text_value = (
                win32gui.GetWindowText(int(hwnd))
                or ""
            ).strip()

            if not text_value:
                return True

            try:
                cls = (
                    win32gui.GetClassName(int(hwnd))
                    or ""
                )
            except Exception:
                cls = ""

            rows.append(
                {
                    "hwnd": int(hwnd),
                    "class": cls,
                    "text": text_value,
                }
            )

        except Exception:
            pass

        return True

    try:
        win32gui.EnumChildWindows(
            int(root_hwnd),
            callback,
            None,
        )
    except Exception:
        pass

    return rows


def _v6103_loaded_evidence_hwnd(
    root_hwnd,
    requested_path: Path,
):
    """
    Robust loaded-CF2 recognition.

    Evidence order:
      1. exact/normalized main-window title match;
      2. requested local-cache basename in a child/status text;
      3. unique 10-char cache digest in a child/status text.

    The child text check fixes long CF2 names for which the Viewer title is
    shortened with "...", while the status bar contains the actual cache path.
    """
    tokens = _v6103_requested_identity_tokens(
        requested_path
    )

    try:
        title = (
            win32gui.GetWindowText(int(root_hwnd))
            or ""
        )
    except Exception:
        title = ""

    if loaded_title_matches_cf2(
        title,
        requested_path,
    ):
        return {
            "matched": True,
            "method": "title-full",
            "title": title,
            "evidence_text": title,
            "evidence_class": "main-window",
        }

    requested_basename = tokens[
        "basename"
    ]

    requested_full_id = tokens[
        "normalized_full"
    ]

    requested_digest = tokens[
        "digest"
    ]

    # V6.10.4 critical fix:
    #
    # CLF Viewer shortens LONG filenames in the middle of the main-window
    # title, for example:
    #
    #   CLF reader/viewer (trial) - AUDIOPERFORMANCE-..._PRO__f0e0004185.CF2
    #
    # The full normalized stem therefore cannot match, but the unique
    # 10-character local-cache digest is still visible at the end of the
    # MAIN TITLE.  This is stronger and cheaper evidence than trying to read
    # a custom-painted status bar.
    title_low = title.lower()

    if (
        requested_digest
        and
        requested_digest
        in title_low
    ):
        return {
            "matched": True,
            "method": "title-cache-digest",
            "title": title,
            "evidence_text": title,
            "evidence_class": "main-window",
        }

    # Diagnostic-only prefix information for logs / future fallbacks.
    # The digest is still the authoritative compact identity.
    requested_prefix = (
        Path(requested_path).stem.split("__", 1)[0][:24].lower()
    )

    best = None

    for item in _v6103_native_child_text_records(
        root_hwnd
    ):
        raw = item[
            "text"
        ]

        low = raw.lower()

        normalized = _normalize_cf2_identity(
            raw
        )

        method = None
        score = 0

        if (
            requested_basename
            and
            requested_basename
            in low
        ):
            method = "child-basename"
            score = 300

        elif (
            requested_full_id
            and
            requested_full_id
            in normalized
        ):
            method = "child-normalized-full"
            score = 250

        elif (
            requested_digest
            and
            requested_digest
            in low
        ):
            method = "child-cache-digest"
            score = 200

        if method is None:
            continue

        cls_low = (
            item[
                "class"
            ]
            or ""
        ).lower()

        # Prefer status-bar controls if several descendants contain the token.
        if (
            "status"
            in cls_low
            or
            "msctls_statusbar32"
            in cls_low
        ):
            score += 100

        # A visible CF2 path/string is stronger than a generic child.
        if ".cf2" in low:
            score += 50

        candidate = {
            "matched": True,
            "method": method,
            "score": score,
            "title": title,
            "evidence_text": raw,
            "evidence_class": item[
                "class"
            ],
            "evidence_hwnd": item[
                "hwnd"
            ],
        }

        if (
            best is None
            or
            candidate[
                "score"
            ]
            >
            best[
                "score"
            ]
        ):
            best = candidate

    if best is not None:
        return best

    return {
        "matched": False,
        "method": None,
        "title": title,
        "evidence_text": "",
        "evidence_class": "",
    }


def _v6103_loaded_evidence_win(
    win,
    requested_path: Path,
):
    try:
        hwnd = int(
            win.handle
        )
    except Exception:
        return {
            "matched": False,
            "method": None,
            "title": "",
            "evidence_text": "",
            "evidence_class": "",
        }

    return _v6103_loaded_evidence_hwnd(
        hwnd,
        requested_path,
    )


def control_text(ctrl) -> str:
    for getter in (lambda: ctrl.window_text(), lambda: ctrl.element_info.name):
        try:
            x = getter()
            if x is not None:
                x = str(x).strip()
                if x:
                    return x
        except Exception:
            pass
    return ""


def connect_viewer(title_re: str):
    pattern = re.compile(title_re, re.IGNORECASE)
    diagnostics = []

    for backend in ("uia", "win32"):
        try:
            desktop = Desktop(backend=backend)
            candidates = []
            for w in desktop.windows():
                try:
                    title = w.window_text() or ""
                    if not pattern.search(title):
                        continue
                    if not w.is_visible():
                        continue
                    r = w.rectangle()
                    area = max(0, int(r.width())) * max(0, int(r.height()))
                    if area < 120000:
                        continue
                    score = area
                    low = title.lower()
                    if "clf reader/viewer" in low:
                        score += 10_000_000
                    if ".cf2" in low:
                        score += 1_000_000
                    candidates.append((score, title, w))
                except Exception:
                    continue
            if candidates:
                candidates.sort(key=lambda x: x[0], reverse=True)
                _, title, win = candidates[0]
                try:
                    win.wait("exists visible enabled ready", timeout=5)
                except Exception:
                    pass
                return backend, win
            diagnostics.append(f"{backend}: no visible matching main window")
        except Exception as exc:
            diagnostics.append(f"{backend}: {exc}")

    raise RuntimeError("No pude conectar con la ventana principal de CLF Viewer. " + " | ".join(diagnostics))


def _dialog_texts(win) -> List[str]:
    texts = []
    try:
        title = win.window_text() or ""
        if title.strip():
            texts.append(title.strip())
    except Exception:
        pass
    try:
        descendants = win.descendants()
    except Exception:
        descendants = []
    for ctrl in descendants:
        t = control_text(ctrl)
        if t:
            texts.append(t)
    out = []
    seen = set()
    for t in texts:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _extract_open_error_message(texts: List[str]) -> Optional[str]:
    joined = " | ".join(texts)
    low = joined.lower()
    patterns = [
        "file does not exist",
        "file not found",
        "cannot open",
        "could not open",
        "erro",
        "error",
        "não existe",
        "no existe",
    ]
    for p in patterns:
        if p in low:
            return joined
    return None


def _dismiss_dialog(dlg):
    for spec in [
        dict(title_re=r"^(ok|aceptar|accept)$", control_type="Button"),
        dict(title_re=r"^(ok|aceptar|accept)$"),
    ]:
        try:
            btn = dlg.child_window(**spec)
            if btn.exists(timeout=0.2):
                click_control(btn)
                time.sleep(0.10)
                return
        except Exception:
            pass
    for keys in ("{ENTER}", "{ESC}", "%{F4}"):
        try:
            dlg.set_focus()
        except Exception:
            pass
        keyboard.send_keys(keys)
        time.sleep(0.10)
        try:
            if not dlg.exists(timeout=0.2):
                return
        except Exception:
            return


def _is_clf_titled_window(win) -> bool:
    try:
        title = (win.window_text() or "").strip().lower()
    except Exception:
        return False
    return bool(title and "clf" in title and ("viewer" in title or "reader" in title))


def _find_open_error_dialog(main_win):
    main_handle = getattr(main_win, "handle", None)
    for backend in ("win32", "uia"):
        try:
            desktop = Desktop(backend=backend)
            for w in desktop.windows():
                try:
                    if not w.is_visible():
                        continue
                    handle = getattr(w, "handle", None)
                    if main_handle is not None and handle == main_handle:
                        continue
                    texts = _dialog_texts(w)
                    if not texts:
                        continue
                    msg = _extract_open_error_message(texts)
                    if not msg:
                        continue
                    if _is_clf_titled_window(w):
                        return w, msg
                    try:
                        owner = w.parent()
                        if owner is not None and getattr(owner, "handle", None) == main_handle:
                            return w, msg
                    except Exception:
                        pass
                except Exception:
                    continue
        except Exception:
            continue
    return None, None


def _clear_stale_open_error_dialogs(main_win, max_dialogs: int = 4) -> int:
    dismissed = 0
    for _ in range(max_dialogs):
        dlg, msg = _find_open_error_dialog(main_win)
        if dlg is None:
            break
        print(f"    stale ERROR DIALOG: {msg}")
        _dismiss_dialog(dlg)
        dismissed += 1
        time.sleep(0.10)
    return dismissed


def _find_loaded_viewer(path: Path, title_re: str, preferred_win=None):
    req = path.resolve()
    candidates = []
    pattern = re.compile(title_re, re.IGNORECASE)
    preferred_handle = getattr(preferred_win, "handle", None) if preferred_win is not None else None

    for backend in ("uia", "win32"):
        try:
            desktop = Desktop(backend=backend)
            for w in desktop.windows():
                try:
                    title = w.window_text() or ""
                    if not pattern.search(title):
                        continue
                    if not w.is_visible():
                        continue
                    r = w.rectangle()
                    area = max(0, int(r.width())) * max(0, int(r.height()))
                    if area < 120000:
                        continue
                    match = loaded_title_matches_cf2(
                        title,
                        req,
                    )

                    # Long filenames may be shortened in the main title.
                    # Check the candidate's native child/status texts as well.
                    if not match:
                        try:
                            native_evidence = _v6103_loaded_evidence_hwnd(
                                int(w.handle),
                                req,
                            )

                            match = bool(
                                native_evidence.get(
                                    "matched"
                                )
                            )
                        except Exception:
                            pass

                    score = area
                    if getattr(w, "handle", None) == preferred_handle:
                        score += 50_000_000
                    if ".cf2" in title.lower():
                        score += 1_000_000
                    if match:
                        score += 100_000_000
                    candidates.append((match, score, backend, w, title))
                except Exception:
                    continue
        except Exception:
            continue

    if not candidates:
        return None, None, None

    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    match, _, backend, win, title = candidates[0]
    if match:
        return backend, win, title
    return None, None, None


def _find_open_file_dialog(main_win, timeout: float, poll_interval: float):
    deadline = time.time() + timeout
    title_patterns = [r".*Open a CLF binary-file.*", r".*(Open|Abrir).*"]
    while time.time() < deadline:
        for backend in ("win32", "uia"):
            for title_re in title_patterns:
                try:
                    candidate = Desktop(backend=backend).window(title_re=title_re)
                    if candidate.exists(timeout=0.15) and candidate.is_visible():
                        try:
                            candidate.wait("exists visible ready", timeout=0.4)
                        except Exception:
                            pass
                        return candidate
                except Exception:
                    pass
        dlg, msg = _find_open_error_dialog(main_win)
        if dlg is not None:
            return dlg
        time.sleep(poll_interval)
    return None


def _submit_open_dialog(dlg):
    full_path = str(dlg._cf2_request_path)
    typed = False
    try:
        edits = dlg.descendants(control_type="Edit")
        if edits:
            edit = edits[-1]
            try:
                edit.set_edit_text(full_path)
            except Exception:
                edit.click_input()
                keyboard.send_keys("^a")
                keyboard.send_keys(full_path, with_spaces=True)
            typed = True
    except Exception:
        pass
    if not typed:
        try:
            keyboard.send_keys("%n")
            time.sleep(0.10)
            keyboard.send_keys("^a")
            keyboard.send_keys(full_path, with_spaces=True)
            typed = True
        except Exception:
            pass
    if not typed:
        raise RuntimeError("No pude escribir el path en File name.")
    time.sleep(0.12)
    keyboard.send_keys("{ENTER}")


def _wait_for_cf2_open_result(main_win, requested_path: Path, title_re: str, timeout: float, poll_interval: float, post_success_wait: float):
    deadline = time.time() + timeout
    while time.time() < deadline:
        dlg, msg = _find_open_error_dialog(main_win)
        if dlg is not None:
            print(f"    ERROR DIALOG: {msg}")
            _dismiss_dialog(dlg)
            print("    dialog dismissed")
            raise RuntimeError(f"CLF Viewer could not open file '{requested_path.name}': {msg}")
        backend, win, title = _find_loaded_viewer(requested_path, title_re, preferred_win=main_win)
        if win is not None:
            try:
                win.set_focus()
            except Exception:
                pass
            if post_success_wait > 0:
                time.sleep(post_success_wait)
            print(f"    loaded confirmed: {requested_path.name}")
            return backend, win, title
        time.sleep(poll_interval)
    raise RuntimeError(f"Timeout esperando a que se cargara '{requested_path.name}'")



def trigger_open_distribution_menu_v65(
    win,
    mode: str = "uia",
):
    """
    Trigger ONLY File -> Open Distribution Binary.

    mode='uia':
        Exact V6.4 baseline behavior.

    mode='win32':
        Connect to the SAME main-window HWND with pywinauto backend='win32'
        and call the same menu_select path.

    This helper does not touch the file dialog, filename, ENTER, error handling,
    loaded-title verification, or any later part of the stable transaction.
    """
    if mode == "uia":
        win.menu_select(
            "File->Open Distribution Binary..."
        )

        return "uia-menu-select"

    if mode != "win32":
        raise ValueError(
            f"Unknown open trigger mode: {mode}"
        )

    hwnd = int(
        win.handle
    )

    app32 = Application(
        backend="win32"
    ).connect(
        handle=hwnd
    )

    win32_win = app32.window(
        handle=hwnd
    )

    try:
        win32_win.set_focus()
    except Exception:
        pass

    win32_win.menu_select(
        "File->Open Distribution Binary..."
    )

    return "win32-menu-select"


def open_cf2_in_viewer(
    win,
    path: Path,
    title_re: str,
    open_timeout: float,
    open_wait: float,
    poll_interval: float,
    open_trigger_mode: str = "uia",
):
    """
    Exact V6.2 stable opening logic, with phase timing only.

    IMPORTANT: behavior/order intentionally unchanged.
    """
    t0 = time.perf_counter()

    t_phase = time.perf_counter()
    try:
        win.set_focus()
    except Exception:
        pass
    focus_seconds = time.perf_counter() - t_phase

    t_phase = time.perf_counter()
    stale = _clear_stale_open_error_dialogs(win)
    stale_seconds = time.perf_counter() - t_phase

    if stale:
        print(f"    stale error dialog(s) dismissed: {stale}")

    t_phase = time.perf_counter()
    dlg = _find_existing_open_dialog_fast()
    existing_dialog_seconds = time.perf_counter() - t_phase

    menu_seconds = 0.0
    dialog_wait_seconds = 0.0

    if dlg is not None:
        print('    reusing existing Open dialog')
    else:
        opened_menu = False

        t_menu = time.perf_counter()
        try:
            trigger_name = trigger_open_distribution_menu_v65(
                win,
                mode=open_trigger_mode,
            )

            opened_menu = True

            print(
                f"    open trigger: {trigger_name}"
            )

        except Exception as exc:
            print(
                f"    primary open trigger failed "
                f"({open_trigger_mode}): {exc}"
            )

        menu_seconds = time.perf_counter() - t_menu

        if not opened_menu:
            t_menu = time.perf_counter()

            keyboard.send_keys('%f')
            time.sleep(0.15)
            keyboard.send_keys('{HOME}')
            keyboard.send_keys('{DOWN 3}')
            keyboard.send_keys('{ENTER}')

            print('    open trigger: keyboard menu fallback')

            menu_seconds += time.perf_counter() - t_menu

        print('    waiting for Open dialog...')

        t_dialog = time.perf_counter()

        dlg = _find_open_file_dialog(
            win,
            timeout=min(
                4.0,
                max(
                    1.5,
                    open_timeout * 0.35,
                ),
            ),
            poll_interval=poll_interval,
        )

        dialog_wait_seconds = time.perf_counter() - t_dialog

    if dlg is None:
        raise RuntimeError(
            'No apareció el diálogo Open Distribution Binary dentro del timeout.'
        )

    t_phase = time.perf_counter()
    texts = _dialog_texts(dlg)
    msg = _extract_open_error_message(texts)
    dialog_inspect_seconds = time.perf_counter() - t_phase

    if msg:
        print(f"    ERROR DIALOG: {msg}")
        _dismiss_dialog(dlg)
        raise RuntimeError(
            f"CLF Viewer could not open file '{path.name}': {msg}"
        )

    dlg._cf2_request_path = path.resolve()

    try:
        dlg.set_focus()
    except Exception:
        pass

    submit_seconds = 0.0
    load_wait_seconds = 0.0

    try:
        t_submit = time.perf_counter()
        _submit_open_dialog(dlg)
        submit_seconds = time.perf_counter() - t_submit

        t_load = time.perf_counter()

        result = _wait_for_cf2_open_result(
            win,
            path.resolve(),
            title_re,
            timeout=open_timeout,
            poll_interval=poll_interval,
            post_success_wait=open_wait,
        )

        load_wait_seconds = time.perf_counter() - t_load

    except Exception:
        try:
            _cleanup_open_dialog_fast()
        except Exception:
            pass
        raise

    total_seconds = time.perf_counter() - t0

    print(
        "    open phases: "
        f"focus={focus_seconds:.3f}s | "
        f"stale={stale_seconds:.3f}s | "
        f"existing-dialog-check={existing_dialog_seconds:.3f}s | "
        f"menu={menu_seconds:.3f}s | "
        f"dialog-wait={dialog_wait_seconds:.3f}s | "
        f"dialog-inspect={dialog_inspect_seconds:.3f}s | "
        f"submit={submit_seconds:.3f}s | "
        f"viewer-load={load_wait_seconds:.3f}s"
    )

    print(
        f"    open transaction: {total_seconds:.3f}s"
    )

    return result


def assert_loaded_cf2(win, path: Path) -> str:
    evidence = _v6103_loaded_evidence_win(
        win,
        path,
    )

    if not evidence.get(
        "matched"
    ):
        raise RuntimeError(
            "El viewer no parece tener cargado el CF2 solicitado. "
            f"viewer={evidence.get('title')!r} "
            f"requested={path.name!r}"
        )

    title = evidence.get(
        "title"
    ) or ""

    # Keep this concise because assert_loaded_cf2 is called more than once.
    return title


def close_extra_clf_viewer_windows(keep_win, title_re: str):
    pattern = re.compile(title_re, re.IGNORECASE)
    keep_handle = getattr(keep_win, "handle", None)
    closed = 0
    for backend in ("win32", "uia"):
        try:
            desktop = Desktop(backend=backend)
            for w in desktop.windows():
                try:
                    title = w.window_text() or ""
                    if not pattern.search(title):
                        continue
                    if not w.is_visible():
                        continue
                    if getattr(w, "handle", None) == keep_handle:
                        continue
                    r = w.rectangle()
                    area = max(0, int(r.width())) * max(0, int(r.height()))
                    if area < 120000:
                        continue
                    try:
                        w.close()
                    except Exception:
                        try:
                            w.set_focus()
                        except Exception:
                            pass
                        keyboard.send_keys("%{F4}")
                    closed += 1
                except Exception:
                    continue
        except Exception:
            continue
    return closed


def find_balloon_spectra_toggle(win):
    candidates = []
    texts = ["Balloon-spectra", "Balloon spectra"]
    for ctrl in win.descendants():
        try:
            ctype = getattr(ctrl.element_info, "control_type", "") or ""
        except Exception:
            ctype = ""
        txt = control_text(ctrl)
        low = txt.lower()
        if any(t.lower() in low for t in texts):
            candidates.append((ctype, txt, ctrl))
    if not candidates:
        raise RuntimeError("No encontré el control Balloon-spectra.")
    # Prefer radio button / button-like.
    candidates.sort(key=lambda x: 0 if str(x[0]).lower() in ("radiobutton", "button") else 1)
    return candidates[0][2]


def activate_balloon_spectra(win, settle: float):
    ctrl = find_balloon_spectra_toggle(win)
    click_control(ctrl)
    time.sleep(max(0.0, settle))


def click_relative(win, xr: float, yr: float, clicks: int = 1, interval: float = 0.0):
    r = win.rectangle()
    x = int(round(r.left + xr * r.width()))
    y = int(round(r.top + yr * r.height()))
    for i in range(clicks):
        mouse.click(button="left", coords=(x, y))
        if interval > 0 and i + 1 < clicks:
            time.sleep(interval)


def home_scrollbars(win, h_back_clicks: int, v_back_clicks: int, h_dec_xy: Tuple[float, float], v_dec_xy: Tuple[float, float], step_settle: float):
    if h_back_clicks > 0:
        click_relative(win, h_dec_xy[0], h_dec_xy[1], clicks=h_back_clicks, interval=max(0.0, step_settle))
    if v_back_clicks > 0:
        click_relative(win, v_dec_xy[0], v_dec_xy[1], clicks=v_back_clicks, interval=max(0.0, step_settle))


def step_horizontal(win, direction: int, h_inc_xy: Tuple[float, float], h_dec_xy: Tuple[float, float], settle: float):
    xy = h_inc_xy if direction > 0 else h_dec_xy
    click_relative(win, xy[0], xy[1])
    time.sleep(max(0.0, settle))


def step_vertical(win, direction: int, v_inc_xy: Tuple[float, float], v_dec_xy: Tuple[float, float], settle: float):
    xy = v_inc_xy if direction > 0 else v_dec_xy
    click_relative(win, xy[0], xy[1])
    time.sleep(max(0.0, settle))


def load_existing_manifest_rows(path: Path) -> List[dict]:
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def save_manifest(path: Path, rows: List[dict]):
    fields = [
        "model",
        "h_index",
        "v_index",
        "h_angle_requested_deg",
        "v_angle_requested_deg",
        "capture_path",
        "angle_label_path",
        "viewer_title",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def inspect_existing_balloon_outputs(root: Path, model: str, h_count: int, v_count: int):
    manifest_path = root / "manifest.csv"
    complete_path = root / "_COMPLETE.txt"
    rows = load_existing_manifest_rows(manifest_path)
    existing = set()
    for row in rows:
        try:
            hi = int(str(row.get("h_index", "")).strip())
            vi = int(str(row.get("v_index", "")).strip())
            p = Path(str(row.get("capture_path", "")))
            if p.is_file():
                existing.add((hi, vi))
        except Exception:
            continue
    expected = {(hi, vi) for vi in range(v_count) for hi in range(h_count)}
    missing = sorted(expected - existing, key=lambda x: (x[1], x[0]))
    all_good = complete_path.is_file() and not missing and bool(existing)
    return {
        "rows": rows,
        "existing": existing,
        "missing": missing,
        "all_good": all_good,
        "has_any_output": bool(existing) or (root / "captures").is_dir(),
        "manifest_path": manifest_path,
        "complete_path": complete_path,
    }


def build_row_map(rows: List[dict]):
    out = {}
    for row in rows:
        try:
            out[(int(row["h_index"]), int(row["v_index"]))] = row
        except Exception:
            pass
    return out



# ----------------------------------------------------------------------
# Balloon-spectra scrollbar discovery and verified stepping
# ----------------------------------------------------------------------

def angle_state_image(win, ratios: Tuple[float, float, float, float], bbox=None) -> Image.Image:
    if bbox is None:
        bbox = absolute_bbox_from_ratios(win, ratios)
    return capture_screen_bbox_fast(win, bbox, fallback_ratios=ratios).convert("RGB")


def angle_state_signature(win, ratios: Tuple[float, float, float, float], bbox=None) -> bytes:
    # Capture ONLY the tiny angle-label rectangle; exact RGB bytes are sufficient
    # to detect a 5-degree state change without OCR.
    return angle_state_image(win, ratios, bbox=bbox).tobytes()


def _rect_tuple(ctrl):
    r = ctrl.rectangle()
    return int(r.left), int(r.top), int(r.right), int(r.bottom)


def find_balloon_scrollbars(win):
    """Find the native horizontal and vertical scrollbars around Balloon-spectra.

    We identify them by geometry rather than fixed window-relative coordinates.
    This survives window-size/DPI changes much better than V1.
    """
    wr = win.rectangle()
    ww = max(1, int(wr.width()))
    wh = max(1, int(wr.height()))
    candidates = []

    try:
        controls = win.descendants(control_type="ScrollBar")
    except Exception:
        try:
            controls = win.descendants()
        except Exception:
            controls = []

    for c in controls:
        try:
            r = c.rectangle()
            width = int(r.width())
            height = int(r.height())
            if width <= 0 or height <= 0:
                continue
            cx = (r.left + r.right) / 2.0
            cy = (r.top + r.bottom) / 2.0
            rx = (cx - wr.left) / ww
            ry = (cy - wr.top) / wh
            # Balloon-spectra lives in the lower-right part of the viewer.
            if rx < 0.50 or ry < 0.43:
                continue
            orientation = None
            if width >= max(30, 2.5 * height):
                orientation = "horizontal"
            elif height >= max(30, 2.5 * width):
                orientation = "vertical"
            if orientation:
                candidates.append((orientation, width * height, rx, ry, c))
        except Exception:
            continue

    horizontal = [x for x in candidates if x[0] == "horizontal"]
    vertical = [x for x in candidates if x[0] == "vertical"]
    horizontal.sort(key=lambda x: (x[3], x[1]), reverse=True)
    vertical.sort(key=lambda x: (x[2], x[1]), reverse=True)

    h = horizontal[0][4] if horizontal else None
    v = vertical[0][4] if vertical else None

    if h is not None and v is not None:
        print(f"    horizontal scrollbar: {_rect_tuple(h)}")
        print(f"    vertical scrollbar:   {_rect_tuple(v)}")
        return h, v

    print("    WARNING: native ScrollBar controls not found; using geometry fallback")
    return None, None


def _scroll_arrow_point(win, ctrl, orientation: str, direction: int):
    """Return a robust click point for one line-step arrow.

    direction < 0 = left/up, direction > 0 = right/down.
    If ctrl is unavailable, use conservative coordinates measured from the
    Balloon-spectra panel visible in the current CLF Viewer layout.
    """
    if ctrl is not None:
        r = ctrl.rectangle()
        if orientation == "horizontal":
            pad = max(4, min(8, int(r.height() * 0.45)))
            x = int(r.left + pad) if direction < 0 else int(r.right - pad)
            y = int((r.top + r.bottom) / 2)
            return x, y
        pad = max(4, min(8, int(r.width() * 0.45)))
        x = int((r.left + r.right) / 2)
        y = int(r.top + pad) if direction < 0 else int(r.bottom - pad)
        return x, y

    r = win.rectangle()
    if orientation == "horizontal":
        xr = 0.600 if direction < 0 else 0.966
        yr = 0.930
    else:
        xr = 0.586
        yr = 0.548 if direction < 0 else 0.916
    return int(r.left + xr * r.width()), int(r.top + yr * r.height())


def click_scroll_line(win, ctrl, orientation: str, direction: int):
    x, y = _scroll_arrow_point(win, ctrl, orientation, direction)
    mouse.click(button="left", coords=(x, y))


def step_scroll_verified(
    win,
    ctrl,
    orientation: str,
    direction: int,
    angle_ratios: Tuple[float, float, float, float],
    settle: float,
    timeout: float,
    retries: int = 2,
    before_signature: bytes = None,
    angle_bbox=None,
):
    """Click one line-step and require a changed angle label.

    FAST V3: the caller may pass the already-known current signature, avoiding
    one screenshot before every movement. Polling captures only the tiny angle
    label rectangle instead of the whole CLF Viewer window.
    """
    before = before_signature
    if before is None:
        before = angle_state_signature(win, angle_ratios, bbox=angle_bbox)

    for attempt in range(retries + 1):
        click_scroll_line(win, ctrl, orientation, direction)
        deadline = time.time() + max(timeout, settle)
        if settle > 0:
            time.sleep(settle)
        while time.time() < deadline:
            after = angle_state_signature(win, angle_ratios, bbox=angle_bbox)
            if after != before:
                return True, after
            time.sleep(0.008)

    return False, before


def home_scroll_verified(
    win,
    ctrl,
    orientation: str,
    direction: int,
    angle_ratios,
    settle: float,
    timeout: float,
    max_steps: int,
    angle_bbox=None,
):
    changed = 0
    for _ in range(max_steps):
        ok, _ = step_scroll_verified(
            win, ctrl, orientation, direction,
            angle_ratios, settle, timeout, retries=0, angle_bbox=angle_bbox
        )
        if not ok:
            return changed
        changed += 1
    raise RuntimeError(
        f"No encontré el extremo de la scrollbar {orientation} después de {max_steps} pasos."
    )


def discover_axis_states(
    win,
    ctrl,
    orientation: str,
    angle_ratios,
    settle: float,
    timeout: float,
    max_states: int,
    angle_bbox=None,
):
    """Assumes the axis is at its decrement endpoint. Returns number of states."""
    count = 1
    while count < max_states:
        ok, _ = step_scroll_verified(
            win, ctrl, orientation, +1,
            angle_ratios, settle, timeout, retries=1, angle_bbox=angle_bbox
        )
        if not ok:
            return count
        count += 1
    raise RuntimeError(
        f"La scrollbar {orientation} superó max_states={max_states}; abortando para evitar loop."
    )


def parse_complete_marker(path: Path) -> Dict[str, str]:
    data = {}
    if not path.is_file():
        return data
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip()
    except Exception:
        pass
    return data


def inspect_existing_v2(root: Path):
    manifest_path = root / "manifest.csv"
    complete_path = root / "_COMPLETE.txt"
    rows = load_existing_manifest_rows(manifest_path)
    row_map = {}
    for row in rows:
        try:
            xi = int(row["horizontal_index"])
            yi = int(row["vertical_index"])
            p = Path(row["capture_path"])
            a_text = str(row.get("angle_label_path", "")).strip()
            angle_ok = (not a_text) or Path(a_text).is_file()
            if p.is_file() and angle_ok:
                row_map[(xi, yi)] = row
        except Exception:
            continue

    marker = parse_complete_marker(complete_path)
    try:
        hs = int(marker.get("horizontal_states", "0"))
        vs = int(marker.get("vertical_states", "0"))
    except Exception:
        hs = vs = 0

    expected = hs * vs if hs > 0 and vs > 0 else 0
    all_good = complete_path.is_file() and expected > 0 and len(row_map) == expected
    return {
        "rows": rows,
        "row_map": row_map,
        "all_good": all_good,
        "has_any_output": bool(rows) or (root / "captures").is_dir(),
        "horizontal_states": hs,
        "vertical_states": vs,
    }


def save_manifest_v2(path: Path, rows: List[dict]):
    fields = [
        "model",
        "horizontal_index",
        "vertical_index",
        "capture_path",
        "angle_label_path",
        "viewer_title",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".part.{os.getpid()}")
    try:
        with tmp.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(str(tmp), str(path))
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8"):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".part.{os.getpid()}")
    try:
        tmp.write_text(text, encoding=encoding)
        os.replace(str(tmp), str(path))
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


class ModelClaim:
    """Atomic per-model claim stored in the shared output directory."""

    def __init__(self, lock_dir: Path, worker_id: str, cf2_path: Path, heartbeat_seconds: float):
        self.lock_dir = lock_dir
        self.worker_id = worker_id
        self.cf2_path = cf2_path
        self.heartbeat_seconds = max(2.0, float(heartbeat_seconds))
        self._stop = threading.Event()
        self._thread = None

    @property
    def heartbeat_path(self):
        return self.lock_dir / "heartbeat"

    def start(self):
        meta = {
            "worker_id": self.worker_id,
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "cf2": str(self.cf2_path),
            "started_epoch": time.time(),
        }
        atomic_write_text(
            self.lock_dir / "owner.json",
            json.dumps(meta, indent=2, ensure_ascii=False),
        )
        self.touch()
        self._thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._thread.start()

    def touch(self):
        now = time.time()
        try:
            self.heartbeat_path.touch(exist_ok=True)
            os.utime(str(self.heartbeat_path), (now, now))
        except Exception:
            pass

    def _heartbeat_loop(self):
        while not self._stop.wait(self.heartbeat_seconds):
            self.touch()

    def release(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        try:
            shutil.rmtree(self.lock_dir)
        except FileNotFoundError:
            pass
        except Exception as exc:
            print(f"    WARNING: could not remove claim {self.lock_dir}: {exc}")


def _claim_is_stale(lock_dir: Path, stale_seconds: float) -> bool:
    heartbeat = lock_dir / "heartbeat"
    try:
        mtime = heartbeat.stat().st_mtime if heartbeat.exists() else lock_dir.stat().st_mtime
    except FileNotFoundError:
        return False
    return (time.time() - mtime) > stale_seconds


def try_claim_model(
    claims_dir: Path,
    model: str,
    worker_id: str,
    cf2_path: Path,
    stale_seconds: float,
    heartbeat_seconds: float,
):
    claims_dir.mkdir(parents=True, exist_ok=True)
    lock_dir = claims_dir / f"{model}.lock"

    for _ in range(2):
        try:
            lock_dir.mkdir()  # atomic on the shared filesystem
            claim = ModelClaim(lock_dir, worker_id, cf2_path, heartbeat_seconds)
            claim.start()
            return claim, "claimed"
        except FileExistsError:
            if not _claim_is_stale(lock_dir, stale_seconds):
                owner = ""
                try:
                    meta = json.loads((lock_dir / "owner.json").read_text(encoding="utf-8"))
                    owner = str(meta.get("worker_id", ""))
                except Exception:
                    pass
                return None, (f"busy by {owner}" if owner else "busy")

            stale_name = claims_dir / (
                f"{model}.stale.{int(time.time())}.{safe_model_name(Path(worker_id))}"
            )
            try:
                lock_dir.rename(stale_name)
                try:
                    shutil.rmtree(stale_name)
                except Exception:
                    pass
                print(f"    reclaimed stale claim: {model}")
            except (FileNotFoundError, FileExistsError, OSError):
                return None, "busy/stale-race"

    return None, "busy"




def quick_complete_state(
    root: Path,
    expected_h: int | None = None,
    expected_v: int | None = None,
):
    """
    Cheap completion test for distributed scheduling.

    _COMPLETE.txt is written only after a model has passed the full on-disk
    validation, so the scheduler does not need to stat thousands of PNG files
    merely to decide whether a model is globally finished.
    """
    complete_path = root / "_COMPLETE.txt"

    if not complete_path.is_file():
        return False

    marker = parse_complete_marker(
        complete_path
    )

    try:
        hs = int(
            marker.get(
                "horizontal_states",
                "0",
            )
        )
        vs = int(
            marker.get(
                "vertical_states",
                "0",
            )
        )
        captures = int(
            marker.get(
                "captures",
                "0",
            )
        )
    except Exception:
        return False

    if hs <= 0 or vs <= 0:
        return False

    if captures != hs * vs:
        return False

    if expected_h is not None and hs != expected_h:
        return False

    if expected_v is not None and vs != expected_v:
        return False

    return True


def summarize_distributed_state(
    files,
    output_dir: Path,
    claims_dir: Path,
    stale_seconds: float,
    expected_h: int | None = None,
    expected_v: int | None = None,
    progress_every: int = 0,
):
    """
    FAST global progress snapshot.

    This intentionally checks only _COMPLETE.txt + claim metadata. It does not
    open manifest.csv or stat thousands of PNG files. Deep validation remains
    local to the model that a worker actually claims.
    """
    complete_models = []
    busy_models = []
    available_models = []

    total = len(files)

    for i, cf2_path in enumerate(files, start=1):
        model = safe_model_name(cf2_path)
        root = output_dir / model

        done = quick_complete_state(
            root,
            expected_h=expected_h,
            expected_v=expected_v,
        )

        if done:
            complete_models.append(model)
        else:
            lock_dir = claims_dir / f"{model}.lock"

            if lock_dir.exists() and not _claim_is_stale(
                lock_dir,
                stale_seconds,
            ):
                busy_models.append(model)
            else:
                available_models.append(model)

        if progress_every > 0 and (
            i % progress_every == 0
            or i == total
        ):
            print(
                f"    status scan: {i}/{total} "
                f"(complete={len(complete_models)}, "
                f"busy={len(busy_models)}, "
                f"available={len(available_models)})"
            )

    complete = len(complete_models)
    busy = len(busy_models)
    available = len(available_models)

    return {
        "total": total,
        "complete": complete,
        "pending": total - complete,
        "busy": busy,
        "available": available,
        "complete_models": complete_models,
        "busy_models": busy_models,
        "available_models": available_models,
    }


def print_global_status(summary, prefix="GLOBAL"):
    total = max(1, int(summary["total"]))
    complete = int(summary["complete"])
    pct = 100.0 * complete / total

    print(
        f"    {prefix}: "
        f"complete={complete}/{summary['total']} ({pct:.1f}%), "
        f"pending={summary['pending']}, "
        f"busy={summary['busy']}, "
        f"available={summary['available']}"
    )


def write_worker_progress(
    output_dir: Path,
    worker_id: str,
    pass_index: int,
    summary,
    pass_claimed: int,
    pass_completed: int,
    pass_errors: int,
    idle_passes: int,
):
    payload = {
        "worker_id": worker_id,
        "epoch": time.time(),
        "pass_index": int(pass_index),
        "total": int(summary["total"]),
        "complete": int(summary["complete"]),
        "pending": int(summary["pending"]),
        "busy": int(summary["busy"]),
        "available": int(summary["available"]),
        "pass_claimed": int(pass_claimed),
        "pass_completed": int(pass_completed),
        "pass_errors": int(pass_errors),
        "idle_passes": int(idle_passes),
    }

    atomic_write_text(
        output_dir / f"distributed_status.{worker_id}.json",
        json.dumps(payload, indent=2, ensure_ascii=False),
    )


def write_unresolved_models(
    output_dir: Path,
    worker_id: str,
    summary,
):
    lines = [
        "# Models still incomplete when this worker stopped",
        f"# total={summary['total']}",
        f"# complete={summary['complete']}",
        f"# pending={summary['pending']}",
        f"# busy={summary['busy']}",
        f"# available={summary['available']}",
        "",
    ]

    for model in summary["busy_models"]:
        lines.append(f"BUSY\t{model}")

    for model in summary["available_models"]:
        lines.append(f"PENDING\t{model}")

    atomic_write_text(
        output_dir / f"unresolved_models.{worker_id}.txt",
        "\n".join(lines) + "\n",
    )


# ----------------------------------------------------------------------
# V6 stable-hybrid helpers
# ----------------------------------------------------------------------

BM_GETCHECK = 0x00F0
BM_CLICK = 0x00F5
BST_CHECKED = 1


def preflight_cf2_file(path: Path):
    t0 = time.perf_counter()
    path = path.resolve()
    if not path.is_file():
        raise RuntimeError(f"CF2 input does not exist: {path}")
    st = path.stat()
    if st.st_size <= 0:
        raise RuntimeError(f"CF2 input is empty: {path}")
    try:
        with path.open("rb") as f:
            probe = f.read(32)
    except Exception as exc:
        raise RuntimeError(f"CF2 exists but cannot be read: {path}: {exc}")
    if not probe:
        raise RuntimeError(f"CF2 returned no readable bytes: {path}")
    return {"size": int(st.st_size), "elapsed": time.perf_counter()-t0}


def _safe_cache_name(source_path: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", source_path.stem).strip("._-") or "cf2"
    digest = hashlib.sha1(str(source_path.resolve()).lower().encode("utf-8", errors="replace")).hexdigest()[:10]
    return f"{stem[:70]}__{digest}{source_path.suffix.upper()}"


def prepare_local_cf2_copy(source_path: Path, cache_root: Optional[Path]):
    source_path = source_path.resolve()
    if cache_root is None:
        return source_path, 0.0, False
    t0 = time.perf_counter()
    cache_root.mkdir(parents=True, exist_ok=True)
    target = cache_root / _safe_cache_name(source_path)
    copy_needed = True
    try:
        if target.is_file():
            s1 = source_path.stat(); s2 = target.stat()
            copy_needed = (s1.st_size != s2.st_size or int(s1.st_mtime) != int(s2.st_mtime))
    except Exception:
        copy_needed = True
    if copy_needed:
        shutil.copy2(str(source_path), str(target))
    preflight_cf2_file(target)
    return target.resolve(), time.perf_counter()-t0, copy_needed


def normalize_viewer_window(win, width: Optional[int], height: Optional[int]):
    if not width or not height:
        return 0.0
    t0 = time.perf_counter()
    hwnd = int(win.handle)
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    except Exception:
        pass
    win32gui.MoveWindow(hwnd, 0, 0, int(width), int(height), True)
    return time.perf_counter()-t0


def _enum_descendant_hwnds_fast(root_hwnd):
    out=[]
    def cb(hwnd, _):
        out.append(hwnd); return True
    try:
        win32gui.EnumChildWindows(int(root_hwnd), cb, None)
    except Exception:
        pass
    return out


def _find_button_hwnd_by_text_fast(root_hwnd, wanted):
    wanted_low = wanted.lower()
    candidates=[]
    for hwnd in _enum_descendant_hwnds_fast(root_hwnd):
        try:
            if (win32gui.GetClassName(hwnd) or '').lower() != 'button':
                continue
            txt=(win32gui.GetWindowText(hwnd) or '').strip()
            if not txt: continue
            low=txt.lower()
            if wanted_low not in low: continue
            score=(100 if low==wanted_low else 0)+50
            candidates.append((score, hwnd))
        except Exception:
            pass
    if not candidates: return None
    candidates.sort(reverse=True)
    return candidates[0][1]




def _find_button_hwnd_exact_text_fast(
    root_hwnd,
    wanted,
):
    wanted = str(wanted).strip().upper()
    candidates = []

    for hwnd in _enum_descendant_hwnds_fast(
        root_hwnd
    ):
        try:
            if (
                win32gui.GetClassName(hwnd)
                or ""
            ).lower() != "button":
                continue

            text = (
                win32gui.GetWindowText(hwnd)
                or ""
            ).strip().upper()

            if text != wanted:
                continue

            rect = win32gui.GetWindowRect(hwnd)
            area = max(
                0,
                rect[2] - rect[0],
            ) * max(
                0,
                rect[3] - rect[1],
            )

            candidates.append(
                (
                    -area,
                    int(hwnd),
                )
            )
        except Exception:
            pass

    if not candidates:
        return None

    candidates.sort(reverse=True)
    return candidates[0][1]


def ensure_sft_view(
    win,
    view: str,
    settle: float = 0.12,
):
    view = str(view).strip().upper()

    if view == "KEEP":
        return {
            "view": "keep",
            "changed": False,
            "seconds": 0.0,
        }

    if view not in (
        "S",
        "F",
        "T",
    ):
        raise ValueError(
            f"S/F/T view inválida: {view}"
        )

    t0 = time.perf_counter()

    hwnd = _find_button_hwnd_exact_text_fast(
        int(win.handle),
        view,
    )

    if not hwnd:
        raise RuntimeError(
            f"No encontré botón S/F/T exacto '{view}'."
        )

    try:
        win.set_focus()
    except Exception:
        pass

    _click_hwnd_center(hwnd)

    if settle > 0:
        time.sleep(settle)

    return {
        "view": view,
        "changed": True,
        "hwnd": int(hwnd),
        "seconds": time.perf_counter() - t0,
    }


def _radio_checked_win32(hwnd):
    try:
        value = int(
            win32gui.SendMessage(
                int(hwnd),
                BM_GETCHECK,
                0,
                0,
            )
        )
        return value == BST_CHECKED
    except Exception:
        return None


def _click_hwnd_center(hwnd):
    r = win32gui.GetWindowRect(
        int(hwnd)
    )

    x = int(
        (
            r[0]
            +
            r[2]
        )
        /
        2
    )

    y = int(
        (
            r[1]
            +
            r[3]
        )
        /
        2
    )

    mouse.click(
        button="left",
        coords=(
            x,
            y,
        ),
    )


def _ensure_radio_selected_fast(
    win,
    wanted,
    settle=0.10,
):
    """
    Prefer a real mouse click on the native radio-button rectangle.

    BM_CLICK proved unreliable for these CLF Viewer radio buttons, while
    the benchmark that produced the ~95% Cabinet speed-up used real UI
    interaction. We therefore locate the control cheaply with Win32 but
    activate it like a human click.
    """
    t0 = time.perf_counter()
    main_hwnd = int(
        win.handle
    )

    hwnd = _find_button_hwnd_by_text_fast(
        main_hwnd,
        wanted,
    )

    if hwnd:
        before = _radio_checked_win32(
            hwnd
        )

        if before is True:
            return {
                "changed": False,
                "verified": True,
                "method": "win32-existing",
                "seconds": time.perf_counter() - t0,
            }

        try:
            win.set_focus()
        except Exception:
            pass

        _click_hwnd_center(
            hwnd
        )

        if settle > 0:
            time.sleep(
                settle
            )

        after = _radio_checked_win32(
            hwnd
        )

        if after is True:
            return {
                "changed": True,
                "verified": True,
                "method": "win32-mouse",
                "seconds": time.perf_counter() - t0,
            }

        # Some old/custom controls do not report BM_GETCHECK correctly.
        # The physical click was still sent; fall through to a UIA fallback
        # only when the state explicitly remains unchecked.
        if after is None:
            return {
                "changed": True,
                "verified": False,
                "method": "win32-mouse-unverifiable",
                "seconds": time.perf_counter() - t0,
            }

    # UIA fallback: this is the same interaction style that worked in
    # benchmark_clf_balloon_render_modes_v1.py.
    ctrl = find_control_by_text(
        win,
        wanted,
    )

    selected_before = None

    try:
        selected_before = bool(
            ctrl.iface_selection_item.CurrentIsSelected
        )
    except Exception:
        try:
            selected_before = bool(
                ctrl.is_selected()
            )
        except Exception:
            pass

    if selected_before is True:
        return {
            "changed": False,
            "verified": True,
            "method": "uia-existing",
            "seconds": time.perf_counter() - t0,
        }

    try:
        ctrl.select()
    except Exception:
        try:
            ctrl.click_input()
        except Exception:
            ctrl.invoke()

    if settle > 0:
        time.sleep(
            max(
                0.20,
                settle,
            )
        )

    selected_after = None

    try:
        selected_after = bool(
            ctrl.iface_selection_item.CurrentIsSelected
        )
    except Exception:
        try:
            selected_after = bool(
                ctrl.is_selected()
            )
        except Exception:
            pass

    return {
        "changed": True,
        "verified": selected_after is not False,
        "method": "uia",
        "seconds": time.perf_counter() - t0,
    }


def ensure_capture_modes_fast(
    win,
    upper_mode="cabinet",
    settle=0.10,
):
    upper_text = (
        "Cabinet 3D representation"
        if upper_mode == "cabinet"
        else "3D balloon"
    )

    upper = _ensure_radio_selected_fast(
        win,
        upper_text,
        settle=settle,
    )

    balloon = _ensure_radio_selected_fast(
        win,
        "Balloon-spectra",
        settle=settle,
    )

    if not upper["verified"]:
        print(
            "    WARNING: upper radio click sent but state could not "
            "be verified programmatically."
        )

    if not balloon["verified"]:
        print(
            "    WARNING: Balloon-spectra click sent but state could not "
            "be verified programmatically."
        )

    return {
        "upper_changed": upper["changed"],
        "balloon_changed": balloon["changed"],
        "upper_verified": upper["verified"],
        "balloon_verified": balloon["verified"],
        "upper_method": upper["method"],
        "balloon_method": balloon["method"],
        "seconds": upper["seconds"] + balloon["seconds"],
    }


def derive_balloon_bboxes_from_scrollbars(h_scroll, v_scroll):
    hr=h_scroll.rectangle(); vr=v_scroll.rectangle()
    graph_left=int(vr.right); graph_top=int(vr.top); graph_right=int(hr.right); graph_bottom=int(hr.top)
    if graph_right<=graph_left or graph_bottom<=graph_top:
        raise RuntimeError('Invalid Balloon-spectra geometry from scrollbars')
    graph_bbox=(graph_left,graph_top,graph_right,graph_bottom)
    gw=graph_right-graph_left; gh=graph_bottom-graph_top
    angle_bbox=(graph_left,graph_top,graph_left+max(70,int(gw*0.34)),graph_top+max(36,int(gh*0.17)))
    return graph_bbox, angle_bbox


def _find_existing_open_dialog_fast():
    for backend in ('win32','uia'):
        try:
            dlg=Desktop(backend=backend).window(title_re=r'.*Open a CLF binary-file.*')
            if dlg.exists(timeout=0.08) and dlg.is_visible():
                return dlg
        except Exception:
            pass
    return None


def _cleanup_open_dialog_fast():
    dlg=_find_existing_open_dialog_fast()
    if dlg is None: return False
    try: dlg.set_focus()
    except Exception: pass
    try:
        keyboard.send_keys('{ESC}')
    except Exception:
        try: dlg.close()
        except Exception: return False
    time.sleep(0.05)
    return True



# ----------------------------------------------------------------------
# V4 Win32 direct scrollbar driver + GDI capture
# ----------------------------------------------------------------------

WM_HSCROLL = 0x0114
WM_VSCROLL = 0x0115
SB_THUMBPOSITION = 4
SB_ENDSCROLL = 8
SB_CTL = 2
SIF_ALL = 0x17


class SCROLLINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("fMask", wintypes.UINT),
        ("nMin", ctypes.c_int),
        ("nMax", ctypes.c_int),
        ("nPage", wintypes.UINT),
        ("nPos", ctypes.c_int),
        ("nTrackPos", ctypes.c_int),
    ]


def _make_wparam(command: int, position: int = 0) -> int:
    return (int(command) & 0xFFFF) | ((int(position) & 0xFFFF) << 16)


HOTPATH_SCROLL_READER = "getscrollpos"


def configure_hotpath_scroll_reader(
    mode: str,
):
    global HOTPATH_SCROLL_READER

    if mode not in (
        "getscrollpos",
        "sif-all",
    ):
        raise ValueError(
            f"Unsupported hot-path scroll reader: {mode}"
        )

    HOTPATH_SCROLL_READER = mode


def get_scroll_pos_fast(
    ctrl,
) -> int:
    hwnd = int(
        ctrl.handle
    )

    return int(
        ctypes.windll.user32.GetScrollPos(
            wintypes.HWND(
                hwnd
            ),
            SB_CTL,
        )
    )


def get_scroll_pos_hot(
    ctrl,
) -> int:
    if HOTPATH_SCROLL_READER == "getscrollpos":
        return get_scroll_pos_fast(
            ctrl
        )

    return int(
        get_scroll_info_win32(
            ctrl
        )["pos"]
    )


def get_scroll_info_win32(ctrl) -> Dict[str, int]:
    hwnd = int(ctrl.handle)

    si = SCROLLINFO()
    si.cbSize = ctypes.sizeof(SCROLLINFO)
    si.fMask = SIF_ALL

    ok = ctypes.windll.user32.GetScrollInfo(
        wintypes.HWND(hwnd),
        SB_CTL,
        ctypes.byref(si),
    )

    if not ok:
        raise ctypes.WinError()

    return {
        "min": int(si.nMin),
        "max": int(si.nMax),
        "page": int(si.nPage),
        "pos": int(si.nPos),
        "track_pos": int(si.nTrackPos),
    }


def set_scroll_position_win32(
    ctrl,
    orientation: str,
    target: int,
    retries: int = 1,
):
    info = get_scroll_info_win32(ctrl)
    if target < info["min"] or target > info["max"]:
        raise RuntimeError(
            f"{orientation}: target={target} fuera de rango "
            f"[{info['min']}, {info['max']}]"
        )

    if info["pos"] == target:
        return 0.0

    hwnd = int(ctrl.handle)
    parent = win32gui.GetParent(hwnd)

    if not parent:
        raise RuntimeError(
            f"{orientation}: scrollbar sin parent HWND"
        )

    msg = WM_HSCROLL if orientation == "horizontal" else WM_VSCROLL

    last_actual = info["pos"]

    for _ in range(retries + 1):
        t0 = time.perf_counter()

        # SendMessage blocks until CLF Viewer has processed the state change.
        # That is useful here: once it returns, GetScrollInfo should already
        # report the target state and the graph should be redrawn.
        win32gui.SendMessage(
            parent,
            msg,
            _make_wparam(SB_THUMBPOSITION, target),
            hwnd,
        )

        win32gui.SendMessage(
            parent,
            msg,
            _make_wparam(SB_ENDSCROLL, 0),
            hwnd,
        )

        elapsed = time.perf_counter() - t0
        last_actual = get_scroll_info_win32(ctrl)["pos"]

        if last_actual == target:
            return elapsed

    raise RuntimeError(
        f"{orientation}: SB_THUMBPOSITION target={target}, actual={last_actual}"
    )


def gdi_grab_bbox(bbox) -> Image.Image:
    left, top, right, bottom = [int(x) for x in bbox]
    width = right - left
    height = bottom - top

    if width <= 0 or height <= 0:
        raise RuntimeError(f"GDI bbox inválido: {bbox}")

    desktop_hwnd = win32gui.GetDesktopWindow()
    src_handle = win32gui.GetWindowDC(desktop_hwnd)
    src_dc = win32ui.CreateDCFromHandle(src_handle)
    mem_dc = src_dc.CreateCompatibleDC()

    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(src_dc, width, height)
    mem_dc.SelectObject(bmp)

    try:
        mem_dc.BitBlt(
            (0, 0),
            (width, height),
            src_dc,
            (left, top),
            win32con.SRCCOPY,
        )

        info = bmp.GetInfo()
        bits = bmp.GetBitmapBits(True)

        image = Image.frombuffer(
            "RGB",
            (info["bmWidth"], info["bmHeight"]),
            bits,
            "raw",
            "BGRX",
            0,
            1,
        ).copy()

        return image

    finally:
        try:
            win32gui.DeleteObject(bmp.GetHandle())
        except Exception:
            pass
        try:
            mem_dc.DeleteDC()
        except Exception:
            pass
        try:
            src_dc.DeleteDC()
        except Exception:
            pass
        try:
            win32gui.ReleaseDC(desktop_hwnd, src_handle)
        except Exception:
            pass




class PersistentGDICapture:
    """
    Persistent GDI capture for one fixed ROI.
    Returns copied PIL images safe for async PNG writers.
    """

    def __init__(self, bbox):
        self.left, self.top, self.right, self.bottom = [int(v) for v in bbox]
        self.width = self.right - self.left
        self.height = self.bottom - self.top

        if self.width <= 0 or self.height <= 0:
            raise RuntimeError(f"GDI bbox inválido: {bbox}")

        self.desktop_hwnd = win32gui.GetDesktopWindow()
        self.src_handle = win32gui.GetWindowDC(self.desktop_hwnd)

        if not self.src_handle:
            raise RuntimeError("GetWindowDC(desktop) devolvió 0")

        self.src_dc = win32ui.CreateDCFromHandle(self.src_handle)
        self.mem_dc = self.src_dc.CreateCompatibleDC()
        self.bmp = win32ui.CreateBitmap()
        self.bmp.CreateCompatibleBitmap(self.src_dc, self.width, self.height)
        self.old_obj = self.mem_dc.SelectObject(self.bmp)
        self.closed = False

    def _bitblt(self):
        if self.closed:
            raise RuntimeError("PersistentGDICapture ya fue cerrado")

        self.mem_dc.BitBlt(
            (0, 0),
            (self.width, self.height),
            self.src_dc,
            (self.left, self.top),
            win32con.SRCCOPY,
        )

    def grab_raw(self) -> bytes:
        self._bitblt()
        return self.bmp.GetBitmapBits(True)

    def grab(self) -> Image.Image:
        bits = self.grab_raw()

        return Image.frombuffer(
            "RGB",
            (self.width, self.height),
            bits,
            "raw",
            "BGRX",
            0,
            1,
        ).copy()

    def close(self):
        if self.closed:
            return

        self.closed = True

        try:
            if self.old_obj is not None:
                self.mem_dc.SelectObject(self.old_obj)
        except Exception:
            pass

        try:
            win32gui.DeleteObject(self.bmp.GetHandle())
        except Exception:
            pass

        try:
            self.mem_dc.DeleteDC()
        except Exception:
            pass

        try:
            self.src_dc.DeleteDC()
        except Exception:
            pass

        try:
            win32gui.ReleaseDC(self.desktop_hwnd, self.src_handle)
        except Exception:
            pass


def gdi_capture_bbox(
    bbox,
    capture_ctx=None,
) -> Image.Image:
    if capture_ctx is not None:
        return capture_ctx.grab()

    return gdi_grab_bbox(bbox)



def expected_row_zip_path(
    row_zip_dir: Path,
    model: str,
    vi: int,
) -> Path:
    return row_zip_dir / f"{model}__y{int(vi):03d}__captures.zip"


def scan_existing_row_zip_states(
    row_zip_dir: Path,
    model: str,
):
    """Index valid Balloon-spectra members stored in row ZIP archives."""
    t0 = time.perf_counter()
    states = set()
    member_map = {}

    if not row_zip_dir.is_dir():
        return states, member_map, time.perf_counter() - t0

    rx = _capture_state_re(model)

    try:
        entries = list(os.scandir(str(row_zip_dir)))
    except FileNotFoundError:
        entries = []

    for entry in entries:
        try:
            if not entry.is_file() or not entry.name.lower().endswith(".zip"):
                continue

            archive_path = Path(entry.path)

            with zipfile.ZipFile(str(archive_path), "r") as zf:
                bad = zf.testzip()
                if bad is not None:
                    print(
                        f"    WARNING: corrupt row ZIP ignored: "
                        f"{archive_path.name}; bad member={bad}"
                    )
                    continue

                for member in zf.namelist():
                    name = Path(member).name
                    m = rx.match(name)
                    if not m:
                        continue

                    state = (
                        int(m.group("x")),
                        int(m.group("y")),
                    )
                    states.add(state)
                    member_map[state] = (
                        archive_path,
                        member,
                    )

        except (OSError, zipfile.BadZipFile) as exc:
            print(
                f"    WARNING: invalid row ZIP ignored: "
                f"{entry.name}: {exc}"
            )

    return states, member_map, time.perf_counter() - t0


def write_or_merge_row_zip_from_futures(
    archive_path: Path,
    items,
):
    """
    Resolve PNG encoding futures, merge them with any existing partial row ZIP,
    and atomically replace the archive using ZIP_STORED.

    items is an iterable of (member_name, Future[bytes]).
    """
    new_members = {}
    for member_name, future in items:
        new_members[member_name] = future.result()

    existing_members = {}

    if archive_path.is_file():
        try:
            with zipfile.ZipFile(str(archive_path), "r") as zf:
                if zf.testzip() is None:
                    for member_name in zf.namelist():
                        existing_members[member_name] = zf.read(member_name)
        except (OSError, zipfile.BadZipFile):
            existing_members = {}

    existing_members.update(new_members)

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = archive_path.with_name(
        archive_path.name
        + f".part.{os.getpid()}.{threading.get_ident()}"
    )

    try:
        with zipfile.ZipFile(
            str(tmp),
            mode="w",
            compression=zipfile.ZIP_STORED,
            allowZip64=True,
        ) as zf:
            for member_name in sorted(existing_members):
                zf.writestr(
                    member_name,
                    existing_members[member_name],
                    compress_type=zipfile.ZIP_STORED,
                )

        os.replace(str(tmp), str(archive_path))

    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass

    return {
        "archive": str(archive_path),
        "new_members": len(new_members),
        "total_members": len(existing_members),
        "bytes": archive_path.stat().st_size,
    }


def build_manifest_rows_mixed_storage(
    model: str,
    capture_dir: Path,
    row_zip_dir: Path,
    angle_dir: Path,
    viewer_title: str,
    horizontal_states: int,
    vertical_states: int,
    save_angle_labels: str,
    audit_every: int,
    individual_states,
    zip_member_map,
):
    """
    Preserve the V6.x manifest columns.

    For row-ZIP captures, capture_path is represented as:
        C:\\...\\row.zip::member.png
    """
    angle_names = scan_existing_filenames(angle_dir)
    rows = []

    for vi in range(vertical_states):
        for hi in range(horizontal_states):
            state = (hi, vi)

            if state in individual_states:
                capture_ref = str(
                    expected_capture_path(
                        capture_dir,
                        model,
                        hi,
                        vi,
                    )
                )
            elif state in zip_member_map:
                archive_path, member_name = zip_member_map[state]
                capture_ref = f"{archive_path}::{member_name}"
            else:
                continue

            want_label = want_audit_label(
                save_angle_labels,
                audit_every,
                hi,
                vi,
                horizontal_states,
                vertical_states,
            )

            angle_path = expected_angle_path(
                angle_dir,
                model,
                hi,
                vi,
            )

            rows.append({
                "model": model,
                "horizontal_index": hi,
                "vertical_index": vi,
                "capture_path": capture_ref,
                "angle_label_path": (
                    str(angle_path)
                    if (
                        want_label
                        and angle_path.name in angle_names
                    )
                    else ""
                ),
                "viewer_title": viewer_title,
            })

    return rows


def expected_capture_path(capture_dir: Path, model: str, hi: int, vi: int) -> Path:
    return capture_dir / f"{model}__x{hi:03d}__y{vi:03d}__balloon_spectra.png"


def expected_angle_path(angle_dir: Path, model: str, hi: int, vi: int) -> Path:
    return angle_dir / f"{model}__x{hi:03d}__y{vi:03d}__angle_label.png"


def want_audit_label(
    mode: str,
    audit_every: int,
    hi: int,
    vi: int,
    horizontal_states: int,
    vertical_states: int,
) -> bool:
    if mode == "all":
        return True

    if mode == "off":
        return False

    global_index = vi * horizontal_states + hi

    return (
        global_index % max(1, audit_every) == 0
        or hi in (0, horizontal_states - 1)
        or vi in (0, vertical_states - 1)
    )


def build_complete_manifest_rows(
    model: str,
    capture_dir: Path,
    angle_dir: Path,
    viewer_title: str,
    horizontal_states: int,
    vertical_states: int,
    save_angle_labels: str,
    audit_every: int,
):
    rows = []

    for vi in range(vertical_states):
        for hi in range(horizontal_states):
            capture_path = expected_capture_path(
                capture_dir,
                model,
                hi,
                vi,
            )

            want_label = want_audit_label(
                save_angle_labels,
                audit_every,
                hi,
                vi,
                horizontal_states,
                vertical_states,
            )

            angle_path = expected_angle_path(
                angle_dir,
                model,
                hi,
                vi,
            )

            rows.append({
                "model": model,
                "horizontal_index": hi,
                "vertical_index": vi,
                "capture_path": str(capture_path),
                "angle_label_path": (
                    str(angle_path)
                    if want_label and angle_path.is_file()
                    else ""
                ),
                "viewer_title": viewer_title,
            })

    return rows


def missing_capture_states(
    capture_dir: Path,
    model: str,
    horizontal_states: int,
    vertical_states: int,
):
    missing = []

    for vi in range(vertical_states):
        for hi in range(horizontal_states):
            if not expected_capture_path(
                capture_dir,
                model,
                hi,
                vi,
            ).is_file():
                missing.append((hi, vi))

    return missing




# ----------------------------------------------------------------------
# V5 helpers: Cabinet render mode + async line stepping + visual verification
# ----------------------------------------------------------------------

SB_LINELEFT = 0
SB_LINEUP = 0
SB_LINERIGHT = 1
SB_LINEDOWN = 1


def find_control_by_text(win, wanted: str):
    wanted_low = wanted.lower()
    candidates = []

    for ctrl in win.descendants():
        try:
            txt = control_text(ctrl)
            if not txt:
                continue

            low = txt.lower()

            if wanted_low not in low:
                continue

            ctype = str(
                getattr(
                    ctrl.element_info,
                    "control_type",
                    "",
                )
                or
                ""
            )

            score = 0

            if ctype.lower() == "radiobutton":
                score += 100

            if low == wanted_low:
                score += 50

            candidates.append(
                (
                    score,
                    ctrl,
                    txt,
                    ctype,
                )
            )

        except Exception:
            continue

    if not candidates:
        raise RuntimeError(
            f"No encontré control con texto: {wanted!r}"
        )

    candidates.sort(
        key=lambda x: x[0],
        reverse=True,
    )

    return candidates[0][1]


def activate_upper_render_mode(
    win,
    mode: str = "cabinet",
    settle: float = 0.10,
):
    if mode == "cabinet":
        wanted = "Cabinet 3D representation"
    elif mode == "3d":
        wanted = "3D balloon"
    else:
        raise ValueError(
            f"upper render mode inválido: {mode}"
        )

    ctrl = find_control_by_text(
        win,
        wanted,
    )

    try:
        ctrl.select()
    except Exception:
        try:
            ctrl.click_input()
        except Exception:
            ctrl.invoke()

    time.sleep(
        max(
            0.0,
            settle,
        )
    )

    print(
        f"    upper render mode: {wanted}"
    )


def gdi_signature(
    bbox,
    capture_ctx=None,
) -> int:
    # Exact hash of a very small ROI; only used to verify that the visible
    # angular label changed after a scrollbar state transition.
    return hash(
        gdi_capture_bbox(
            bbox,
            capture_ctx,
        ).tobytes()
    )


def post_scroll_line(
    ctrl,
    orientation: str,
    direction: int,
):
    hwnd = int(
        ctrl.handle
    )

    parent = win32gui.GetParent(
        hwnd
    )

    if not parent:
        raise RuntimeError(
            f"{orientation}: scrollbar sin parent HWND"
        )

    msg = (
        WM_HSCROLL
        if orientation == "horizontal"
        else WM_VSCROLL
    )

    command = (
        SB_LINERIGHT
        if direction > 0
        else SB_LINELEFT
    )

    win32gui.PostMessage(
        parent,
        msg,
        _make_wparam(
            command,
            0,
        ),
        hwnd,
    )


def wait_scroll_target(
    ctrl,
    target: int,
    timeout: float,
    poll_interval: float,
):
    deadline = (
        time.perf_counter()
        +
        timeout
    )

    while time.perf_counter() < deadline:
        actual = get_scroll_pos_hot(
            ctrl
        )

        if actual == target:
            return

        if poll_interval > 0:
            time.sleep(
                poll_interval
            )

    actual = get_scroll_pos_hot(
        ctrl
    )

    raise RuntimeError(
        f"Timeout esperando scrollbar target={target}; actual={actual}"
    )


def wait_visual_signature_change(
    bbox,
    before_signature: int,
    timeout: float,
    poll_interval: float,
    capture_ctx=None,
):
    deadline = (
        time.perf_counter()
        +
        timeout
    )

    while time.perf_counter() < deadline:
        current = gdi_signature(
            bbox,
            capture_ctx,
        )

        if current != before_signature:
            return current

        if poll_interval > 0:
            time.sleep(
                poll_interval
            )

    raise RuntimeError(
        "El ScrollBar cambió de posición, pero el label angular "
        "no cambió dentro del timeout."
    )


def step_line_verified_v5(
    ctrl,
    orientation: str,
    direction: int,
    expected_target: int,
    angle_bbox,
    previous_signature: int,
    move_timeout: float,
    poll_interval: float,
    visual_timeout: float,
    visual_poll_interval: float,
    post_visual_settle: float,
    angle_capture_ctx=None,
):
    before_pos = get_scroll_pos_hot(
        ctrl
    )

    if expected_target == before_pos:
        return (
            previous_signature,
            0.0,
        )

    if expected_target != before_pos + direction:
        raise RuntimeError(
            f"{orientation}: V5 line-step sólo admite vecino inmediato; "
            f"before={before_pos}, target={expected_target}, direction={direction}"
        )

    t0 = time.perf_counter()

    post_scroll_line(
        ctrl,
        orientation,
        direction,
    )

    wait_scroll_target(
        ctrl,
        expected_target,
        timeout=move_timeout,
        poll_interval=poll_interval,
    )

    new_signature = wait_visual_signature_change(
        angle_bbox,
        previous_signature,
        timeout=visual_timeout,
        poll_interval=visual_poll_interval,
        capture_ctx=angle_capture_ctx,
    )

    if post_visual_settle > 0:
        time.sleep(
            post_visual_settle
        )

    elapsed = (
        time.perf_counter()
        -
        t0
    )

    # Final numerical gate immediately before capture.
    actual = get_scroll_pos_hot(
        ctrl
    )

    if actual != expected_target:
        raise RuntimeError(
            f"{orientation}: expected={expected_target}, actual={actual}"
        )

    return (
        new_signature,
        elapsed,
    )


def step_line_hybrid_row_v64(
    ctrl,
    orientation: str,
    direction: int,
    expected_target: int,
    angle_bbox,
    *,
    verify_visual: bool,
    move_timeout: float,
    poll_interval: float,
    visual_timeout: float,
    visual_poll_interval: float,
    post_visual_settle: float,
    angle_capture_ctx=None,
):
    """
    HYBRID-ROW movement.

    Numerical GetScrollInfo verification remains mandatory for EVERY move.
    Visual label verification is performed only when verify_visual=True:
      - every vertical transition;
      - horizontal row endpoint.

    The visual signature is sampled immediately before the audited move,
    matching the full-grid benchmark implementation.
    """
    before_pos = get_scroll_pos_hot(
        ctrl
    )

    if expected_target == before_pos:
        return 0.0, False

    if expected_target != before_pos + direction:
        raise RuntimeError(
            f"{orientation}: hybrid-row sólo admite vecino inmediato; "
            f"before={before_pos}, target={expected_target}, direction={direction}"
        )

    before_signature = None

    if verify_visual:
        before_signature = gdi_signature(
            angle_bbox,
            angle_capture_ctx,
        )

    t0 = time.perf_counter()

    post_scroll_line(
        ctrl,
        orientation,
        direction,
    )

    wait_scroll_target(
        ctrl,
        expected_target,
        timeout=move_timeout,
        poll_interval=poll_interval,
    )

    if verify_visual:
        wait_visual_signature_change(
            angle_bbox,
            before_signature,
            timeout=visual_timeout,
            poll_interval=visual_poll_interval,
            capture_ctx=angle_capture_ctx,
        )

        if post_visual_settle > 0:
            time.sleep(
                post_visual_settle
            )

    # Mandatory final numerical gate.
    actual = get_scroll_pos_hot(
        ctrl
    )

    if actual != expected_target:
        raise RuntimeError(
            f"{orientation}: expected={expected_target}, actual={actual}"
        )

    return (
        time.perf_counter() - t0,
        verify_visual,
    )


def direct_position_verified_v5(
    ctrl,
    orientation: str,
    target: int,
    angle_bbox,
    previous_signature: int,
    visual_timeout: float,
    visual_poll_interval: float,
    post_visual_settle: float,
    angle_capture_ctx=None,
):
    before = get_scroll_pos_hot(
        ctrl
    )

    if before == target:
        return (
            previous_signature,
            0.0,
        )

    t0 = time.perf_counter()

    # Used only for homing / sparse repair.
    # Sequential full sweeps use PostMessage line, which benchmarked much faster.
    set_scroll_position_win32(
        ctrl,
        orientation,
        target,
    )

    new_signature = wait_visual_signature_change(
        angle_bbox,
        previous_signature,
        timeout=visual_timeout,
        poll_interval=visual_poll_interval,
        capture_ctx=angle_capture_ctx,
    )

    if post_visual_settle > 0:
        time.sleep(
            post_visual_settle
        )

    return (
        new_signature,
        time.perf_counter() - t0,
    )



# ----------------------------------------------------------------------
# V6.2 local-output / resume-index helpers
# ----------------------------------------------------------------------

_CAPTURE_STATE_RE_CACHE = {}


def _capture_state_re(model: str):
    rx = _CAPTURE_STATE_RE_CACHE.get(model)

    if rx is None:
        rx = re.compile(
            r"^"
            +
            re.escape(model)
            +
            r"__x(?P<x>\d{3})__y(?P<y>\d{3})__balloon_spectra\.png$",
            re.IGNORECASE,
        )

        _CAPTURE_STATE_RE_CACHE[model] = rx

    return rx


def scan_existing_capture_states(
    capture_dir: Path,
    model: str,
):
    """
    Scan a capture directory once and return {(horizontal_index, vertical_index)}.

    This replaces 2664 Path.is_file() calls during each sweep.
    """
    t0 = time.perf_counter()
    result = set()

    if not capture_dir.is_dir():
        return result, time.perf_counter() - t0

    rx = _capture_state_re(
        model
    )

    try:
        with os.scandir(
            str(capture_dir)
        ) as it:
            for entry in it:
                try:
                    if not entry.is_file():
                        continue
                except OSError:
                    continue

                m = rx.match(
                    entry.name
                )

                if not m:
                    continue

                result.add(
                    (
                        int(m.group("x")),
                        int(m.group("y")),
                    )
                )

    except FileNotFoundError:
        pass

    return (
        result,
        time.perf_counter() - t0,
    )


def scan_existing_filenames(
    directory: Path,
):
    names = set()

    if not directory.is_dir():
        return names

    try:
        with os.scandir(
            str(directory)
        ) as it:
            for entry in it:
                try:
                    if entry.is_file():
                        names.add(
                            entry.name
                        )
                except OSError:
                    pass
    except FileNotFoundError:
        pass

    return names


def prepare_local_output_dirs(
    local_output_root: Optional[Path],
    model: str,
):
    if local_output_root is None:
        return None

    model_root = (
        local_output_root
        /
        model
    )

    capture_dir = (
        model_root
        /
        "captures"
    )

    angle_dir = (
        model_root
        /
        "angle_labels"
    )

    capture_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    angle_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    return {
        "root": model_root,
        "capture_dir": capture_dir,
        "angle_dir": angle_dir,
    }


def sync_directory_new_files(
    source_dir: Path,
    destination_dir: Path,
    workers: int = 4,
):
    """
    Copy missing files from local Windows storage to /shared.

    V6.3 optimizations:
      - one destination directory scan;
      - no metadata-preserving copy2() overhead;
      - configurable parallel copy;
      - .part + os.replace for atomic final visibility.
    """
    t0 = time.perf_counter()

    if not source_dir.is_dir():
        return {
            "copied": 0,
            "reused": 0,
            "bytes": 0,
            "seconds": 0.0,
        }

    destination_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    destination_names = scan_existing_filenames(
        destination_dir
    )

    pending = []
    reused = 0

    with os.scandir(
        str(source_dir)
    ) as it:
        for entry in it:
            try:
                if not entry.is_file():
                    continue
            except OSError:
                continue

            if entry.name in destination_names:
                reused += 1
                continue

            try:
                size = int(
                    entry.stat().st_size
                )
            except OSError:
                size = 0

            pending.append(
                (
                    entry.path,
                    entry.name,
                    size,
                )
            )

    if not pending:
        return {
            "copied": 0,
            "reused": reused,
            "bytes": 0,
            "seconds": time.perf_counter() - t0,
        }

    max_workers = max(
        1,
        int(
            workers
        ),
    )

    def copy_one(item):
        source_path, filename, size = item

        final_path = (
            destination_dir
            /
            filename
        )

        part_path = (
            destination_dir
            /
            (
                filename
                +
                f".part.{os.getpid()}"
            )
        )

        try:
            if part_path.exists():
                part_path.unlink()
        except Exception:
            pass

        try:
            shutil.copyfile(
                source_path,
                str(
                    part_path
                ),
            )

            os.replace(
                str(
                    part_path
                ),
                str(
                    final_path
                ),
            )

            return (
                1,
                size,
                None,
            )

        except Exception as exc:
            try:
                if part_path.exists():
                    part_path.unlink()
            except Exception:
                pass

            return (
                0,
                0,
                (
                    filename,
                    repr(
                        exc
                    ),
                ),
            )

    copied = 0
    copied_bytes = 0
    errors = []

    if max_workers == 1:
        for item in pending:
            ok, size, error = copy_one(
                item
            )

            copied += ok
            copied_bytes += size

            if error:
                errors.append(
                    error
                )
    else:
        with ThreadPoolExecutor(
            max_workers=max_workers
        ) as pool:
            for ok, size, error in pool.map(
                copy_one,
                pending,
            ):
                copied += ok
                copied_bytes += size

                if error:
                    errors.append(
                        error
                    )

    elapsed = (
        time.perf_counter()
        -
        t0
    )

    if errors:
        raise RuntimeError(
            f"Parallel output sync failed for {len(errors)} file(s). "
            f"First errors: {errors[:5]}"
        )

    return {
        "copied": copied,
        "reused": reused,
        "bytes": copied_bytes,
        "seconds": elapsed,
    }


def sync_local_model_output(
    local_model_root: Path,
    shared_model_root: Path,
    save_angle_labels: str,
    workers: int = 4,
):
    t0 = time.perf_counter()

    captures = sync_directory_new_files(
        local_model_root / "captures",
        shared_model_root / "captures",
        workers=workers,
    )

    angles = {
        "copied": 0,
        "reused": 0,
        "bytes": 0,
        "seconds": 0.0,
    }

    if save_angle_labels != "off":
        angles = sync_directory_new_files(
            local_model_root / "angle_labels",
            shared_model_root / "angle_labels",
            workers=workers,
        )

    total_bytes = (
        captures["bytes"]
        +
        angles["bytes"]
    )

    total_seconds = (
        time.perf_counter()
        -
        t0
    )

    mib = (
        total_bytes
        /
        (
            1024.0
            *
            1024.0
        )
    )

    throughput = (
        mib
        /
        max(
            total_seconds,
            1e-9,
        )
    )

    return {
        "captures_copied": captures["copied"],
        "captures_reused": captures["reused"],
        "angles_copied": angles["copied"],
        "angles_reused": angles["reused"],
        "bytes": total_bytes,
        "mib": mib,
        "mib_per_sec": throughput,
        "seconds": total_seconds,
    }


def build_manifest_rows_fast(
    model: str,
    capture_dir: Path,
    angle_dir: Path,
    viewer_title: str,
    horizontal_states: int,
    vertical_states: int,
    save_angle_labels: str,
    audit_every: int,
):
    """
    Build manifest without thousands of capture-path stat() calls.

    Call this only after scan_existing_capture_states() has verified the complete
    capture grid in the shared output directory.
    """
    angle_names = scan_existing_filenames(
        angle_dir
    )

    rows = []

    for vi in range(
        vertical_states
    ):
        for hi in range(
            horizontal_states
        ):
            capture_path = expected_capture_path(
                capture_dir,
                model,
                hi,
                vi,
            )

            want_label = want_audit_label(
                save_angle_labels,
                audit_every,
                hi,
                vi,
                horizontal_states,
                vertical_states,
            )

            angle_path = expected_angle_path(
                angle_dir,
                model,
                hi,
                vi,
            )

            rows.append({
                "model": model,
                "horizontal_index": hi,
                "vertical_index": vi,
                "capture_path": str(
                    capture_path
                ),
                "angle_label_path": (
                    str(angle_path)
                    if (
                        want_label
                        and
                        angle_path.name
                        in
                        angle_names
                    )
                    else
                    ""
                ),
                "viewer_title": viewer_title,
            })

    return rows


def clean_local_model_output(
    local_model_root: Optional[Path],
):
    if local_model_root is None:
        return

    shutil.rmtree(
        str(local_model_root),
        ignore_errors=True,
    )


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------


# ======================================================================
# V6.10 production hot path
#   - hybrid-fast OPEN (validated in benchmark V5/V8)
#   - native Cabinet/scrollbar READY path (validated in V8)
#   - one-neighbor functional readiness probe
#   - automatic fallback to stable V6.9 path
# ======================================================================


def _v610_enum_top_windows():
    out = []

    def callback(hwnd, _):
        try:
            if win32gui.IsWindowVisible(hwnd):
                out.append(int(hwnd))
        except Exception:
            pass
        return True

    win32gui.EnumWindows(callback, None)
    return out


def _v610_child_texts(hwnd):
    values = []

    try:
        text = (win32gui.GetWindowText(int(hwnd)) or "").strip()
        if text:
            values.append(text)
    except Exception:
        pass

    def callback(child, _):
        try:
            text = (win32gui.GetWindowText(child) or "").strip()
            if text:
                values.append(text)
        except Exception:
            pass
        return True

    try:
        win32gui.EnumChildWindows(int(hwnd), callback, None)
    except Exception:
        pass

    return values


def _v610_native_find_error_dialog(main_hwnd):
    for hwnd in _v610_enum_top_windows():
        if int(hwnd) == int(main_hwnd):
            continue

        try:
            if (win32gui.GetClassName(hwnd) or "") != "#32770":
                continue

            owner = win32gui.GetWindow(hwnd, win32con.GW_OWNER)
            title = (win32gui.GetWindowText(hwnd) or "").strip().lower()

            related = (
                int(owner or 0) == int(main_hwnd)
                or (
                    "clf" in title
                    and ("viewer" in title or "reader" in title)
                )
            )

            if not related:
                continue

            msg = _extract_open_error_message(_v610_child_texts(hwnd))
            if msg:
                return int(hwnd), msg

        except Exception:
            pass

    return None, None


def _v610_native_dismiss_dialog(hwnd):
    buttons = []

    def callback(child, _):
        try:
            if (
                win32gui.IsWindowVisible(child)
                and (win32gui.GetClassName(child) or "").lower() == "button"
            ):
                text = (
                    (win32gui.GetWindowText(child) or "")
                    .replace("&", "")
                    .strip()
                    .lower()
                )

                if text in ("ok", "accept", "aceptar"):
                    buttons.append(int(child))
        except Exception:
            pass
        return True

    try:
        win32gui.EnumChildWindows(int(hwnd), callback, None)
    except Exception:
        pass

    try:
        if buttons:
            win32gui.SendMessage(buttons[0], win32con.BM_CLICK, 0, 0)
        else:
            win32gui.PostMessage(int(hwnd), win32con.WM_CLOSE, 0, 0)
        return True
    except Exception:
        return False


def _v610_native_clear_stale_errors(main_hwnd, max_dialogs=4):
    dismissed = 0

    for _ in range(max(1, int(max_dialogs))):
        hwnd, msg = _v610_native_find_error_dialog(main_hwnd)
        if hwnd is None:
            break

        print(f"    V6.10 stale ERROR dismissed: {msg}")
        _v610_native_dismiss_dialog(hwnd)
        dismissed += 1
        time.sleep(0.03)

    return dismissed


def _v610_native_find_open_dialog(main_hwnd=None):
    exact_related = None
    exact_any = None
    fallback_related = None
    fallback_any = None

    for hwnd in _v610_enum_top_windows():
        try:
            if (win32gui.GetClassName(hwnd) or "") != "#32770":
                continue

            title = (win32gui.GetWindowText(hwnd) or "").strip().lower()
            owner = win32gui.GetWindow(hwnd, win32con.GW_OWNER)
            related = (
                main_hwnd is not None
                and int(owner or 0) == int(main_hwnd)
            )

            is_exact = (
                "open a clf binary-file" in title
                or "open a clf binary file" in title
            )

            if is_exact and related:
                exact_related = int(hwnd)
                break
            if is_exact and exact_any is None:
                exact_any = int(hwnd)
            elif title in ("open", "abrir") and related:
                fallback_related = int(hwnd)
            elif title in ("open", "abrir") and fallback_any is None:
                fallback_any = int(hwnd)

        except Exception:
            pass

    return exact_related or exact_any or fallback_related or fallback_any


def _v610_native_wait_open_dialog(main_win, timeout, poll=0.03):
    deadline = time.perf_counter() + float(timeout)
    main_hwnd = int(main_win.handle)

    while time.perf_counter() < deadline:
        dlg = _v610_native_find_open_dialog(main_hwnd)
        if dlg is not None:
            return dlg

        err_hwnd, _ = _v610_native_find_error_dialog(main_hwnd)
        if err_hwnd is not None:
            return err_hwnd

        if poll > 0:
            time.sleep(float(poll))

    return None


def _v610_keyboard_submit(dlg_hwnd, full_path: Path):
    """Use the Windows common-dialog File-name mnemonic validated in V5/V8."""
    try:
        win32gui.SetForegroundWindow(int(dlg_hwnd))
    except Exception:
        pass

    keyboard.send_keys("%n")
    time.sleep(0.03)
    keyboard.send_keys("^a")
    keyboard.send_keys(
        str(full_path.resolve()),
        with_spaces=True,
        pause=0.0,
    )
    time.sleep(0.04)
    keyboard.send_keys("{ENTER}")


def _v610_hybrid_wait_loaded(
    main_win,
    requested_path: Path,
    title_re: str,
    timeout: float,
    post_success_wait: float,
    poll: float = 0.03,
    fallback_after: float = 4.0,
):
    main_hwnd = int(main_win.handle)
    started = time.perf_counter()
    deadline = started + float(timeout)
    next_fallback = started + float(fallback_after)

    while time.perf_counter() < deadline:
        err_hwnd, msg = _v610_native_find_error_dialog(main_hwnd)

        if err_hwnd is not None:
            print(f"    V6.10 ERROR DIALOG: {msg}")
            _v610_native_dismiss_dialog(err_hwnd)
            raise RuntimeError(
                f"CLF Viewer could not open file '{requested_path.name}': {msg}"
            )

        try:
            evidence = _v6103_loaded_evidence_hwnd(
                main_hwnd,
                requested_path,
            )

            if evidence.get(
                "matched"
            ):
                title = evidence.get(
                    "title"
                ) or ""

                print(
                    "    V6.10.4 loaded CF2 evidence: "
                    f"method={evidence.get('method')} | "
                    f"class={evidence.get('evidence_class')!r}"
                )

                if post_success_wait > 0:
                    time.sleep(
                        float(
                            post_success_wait
                        )
                    )

                return (
                    "win32-native-loaded-evidence",
                    main_win,
                    title,
                )

        except Exception:
            pass

        now = time.perf_counter()

        if now >= next_fallback:
            backend, found_win, title = _find_loaded_viewer(
                requested_path,
                title_re,
                preferred_win=main_win,
            )

            if found_win is not None:
                if post_success_wait > 0:
                    time.sleep(float(post_success_wait))
                return backend, found_win, title

            next_fallback = now + 1.5

        if poll > 0:
            time.sleep(float(poll))

    raise RuntimeError(
        f"Timeout esperando a que se cargara '{requested_path.name}'"
    )


def open_cf2_hybrid_fast_v610(
    win,
    path: Path,
    title_re: str,
    open_timeout: float,
    open_wait: float,
):
    """Fast OPEN path validated 6/6 in V5 and used by V8."""
    phases = {
        "focus": 0.0,
        "stale": 0.0,
        "existing": 0.0,
        "menu": 0.0,
        "dialog": 0.0,
        "inspect": 0.0,
        "submit": 0.0,
        "load": 0.0,
        "total": 0.0,
    }

    total_t0 = time.perf_counter()
    main_hwnd = int(win.handle)

    t0 = time.perf_counter()
    try:
        win.set_focus()
    except Exception:
        pass
    phases["focus"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    stale = _v610_native_clear_stale_errors(main_hwnd)
    phases["stale"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    dlg = _v610_native_find_open_dialog(main_hwnd)
    phases["existing"] = time.perf_counter() - t0

    if dlg is None:
        t0 = time.perf_counter()
        trigger_open_distribution_menu_v65(win, mode="win32")
        phases["menu"] = time.perf_counter() - t0

        t0 = time.perf_counter()
        dlg = _v610_native_wait_open_dialog(
            win,
            timeout=min(
                6.0,
                max(2.0, float(open_timeout) * 0.35),
            ),
            poll=0.03,
        )
        phases["dialog"] = time.perf_counter() - t0

    if dlg is None:
        raise RuntimeError(
            "V6.10: Open Distribution Binary dialog did not appear."
        )

    t0 = time.perf_counter()
    texts = _v610_child_texts(dlg)
    msg = _extract_open_error_message(texts)
    phases["inspect"] = time.perf_counter() - t0

    if msg:
        _v610_native_dismiss_dialog(dlg)
        raise RuntimeError(msg)

    t0 = time.perf_counter()
    _v610_keyboard_submit(dlg, path)
    phases["submit"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    result = _v610_hybrid_wait_loaded(
        win,
        path.resolve(),
        title_re,
        timeout=float(open_timeout),
        post_success_wait=float(open_wait),
        poll=0.03,
        fallback_after=4.0,
    )
    phases["load"] = time.perf_counter() - t0
    phases["total"] = time.perf_counter() - total_t0

    print(
        "    V6.10 OPEN hybrid-fast: "
        f"total={phases['total']:.3f}s | "
        f"stale={phases['stale']:.3f}s | "
        f"existing={phases['existing']:.3f}s | "
        f"menu={phases['menu']:.3f}s | "
        f"dialog={phases['dialog']:.3f}s | "
        f"inspect={phases['inspect']:.3f}s | "
        f"submit={phases['submit']:.3f}s | "
        f"load={phases['load']:.3f}s"
    )

    return result



SMTO_BLOCK = 0x0001
SMTO_ABORTIFHUNG = 0x0002
WM_NULL = 0x0000


def _v6102_window_responsive(hwnd, timeout_ms=250):
    """
    Cheap Win32 responsiveness test.

    A CF2 title can change before CLF Viewer has finished processing the file.
    SendMessageTimeout(WM_NULL) distinguishes "title changed" from "the UI
    message loop is responsive again".
    """
    result = ctypes.c_size_t(0)

    try:
        ok = ctypes.windll.user32.SendMessageTimeoutW(
            wintypes.HWND(int(hwnd)),
            wintypes.UINT(WM_NULL),
            wintypes.WPARAM(0),
            wintypes.LPARAM(0),
            wintypes.UINT(SMTO_BLOCK | SMTO_ABORTIFHUNG),
            wintypes.UINT(int(timeout_ms)),
            ctypes.byref(result),
        )
        return bool(ok)
    except Exception:
        return False


def _v6102_control_usable(hwnd):
    if not hwnd:
        return False

    try:
        return (
            bool(win32gui.IsWindow(int(hwnd)))
            and bool(win32gui.IsWindowVisible(int(hwnd)))
            and bool(win32gui.IsWindowEnabled(int(hwnd)))
        )
    except Exception:
        return False


def _v6102_wait_viewer_interactive(
    win,
    requested_path: Path,
    timeout=45.0,
    poll=0.10,
    consecutive=3,
):
    """
    Wait until the newly loaded model is not only named in the title bar, but
    the CLF Viewer GUI is actually responsive and the controls needed by READY
    are live.

    No UIA enumeration is used here.
    """
    started = time.perf_counter()
    deadline = started + float(timeout)
    main_hwnd = int(win.handle)

    stable = 0
    polls = 0
    last = {}

    while time.perf_counter() < deadline:
        polls += 1

        err_hwnd, msg = _v610_native_find_error_dialog(main_hwnd)
        if err_hwnd is not None:
            _v610_native_dismiss_dialog(err_hwnd)
            raise RuntimeError(
                f"CLF Viewer error while waiting for interactive UI: {msg}"
            )

        try:
            title = win32gui.GetWindowText(main_hwnd) or ""
        except Exception:
            title = ""

        loaded_evidence = _v6103_loaded_evidence_hwnd(
            main_hwnd,
            requested_path,
        )

        title_ok = bool(
            loaded_evidence.get(
                "matched"
            )
        )

        responsive = (
            title_ok
            and _v6102_window_responsive(
                main_hwnd,
                timeout_ms=250,
            )
        )

        cabinet = _v610_find_top_cabinet_native(
            main_hwnd
        )

        cabinet_hwnd = (
            int(cabinet[1]["hwnd"])
            if cabinet is not None
            else None
        )

        t_hwnd = _find_button_hwnd_exact_text_fast(
            main_hwnd,
            "T",
        )

        balloon_hwnd = _find_button_hwnd_by_text_fast(
            main_hwnd,
            "Balloon-spectra",
        )

        controls_ok = (
            _v6102_control_usable(cabinet_hwnd)
            and _v6102_control_usable(t_hwnd)
            and _v6102_control_usable(balloon_hwnd)
        )

        last = {
            "title_ok": bool(title_ok),
            "loaded_method": loaded_evidence.get(
                "method"
            ),
            "responsive": bool(responsive),
            "cabinet": bool(_v6102_control_usable(cabinet_hwnd)),
            "T": bool(_v6102_control_usable(t_hwnd)),
            "balloon": bool(_v6102_control_usable(balloon_hwnd)),
            "title": title,
        }

        if title_ok and responsive and controls_ok:
            stable += 1
            if stable >= int(consecutive):
                return {
                    "seconds": time.perf_counter() - started,
                    "polls": polls,
                    **last,
                }
        else:
            stable = 0

        if poll > 0:
            time.sleep(float(poll))

    raise RuntimeError(
        "V6.10.2 viewer-interactive timeout; "
        f"last={last}"
    )


def _v6102_finalize_loaded_result(
    result,
    requested_path: Path,
    interactive_timeout: float,
):
    backend, loaded_win, title = result

    gate = _v6102_wait_viewer_interactive(
        loaded_win,
        requested_path.resolve(),
        timeout=float(interactive_timeout),
        poll=0.10,
        consecutive=3,
    )

    print(
        "    V6.10.4 viewer interactive: "
        f"{gate['seconds']:.3f}s | "
        f"polls={gate['polls']} | "
        f"loaded={gate.get('loaded_method')} | "
        f"Cabinet={gate['cabinet']} "
        f"T={gate['T']} "
        f"Balloon={gate['balloon']}"
    )

    return backend, loaded_win, title


def open_cf2_production_v610(
    win,
    path: Path,
    title_re: str,
    open_timeout: float,
    open_wait: float,
    poll_interval: float,
    open_trigger_mode: str,
    strategy: str = "hybrid-fast",
    fallback: bool = True,
    slow_open_grace: float = 30.0,
    retry_open_timeout: float = 60.0,
    interactive_timeout: float = 45.0,
):
    """
    V6.10.1 OPEN wrapper.

    Important recovery rule:
    if hybrid-fast has already submitted the CF2 and only the load wait times
    out, DO NOT immediately trigger a second Open transaction. CLF Viewer may
    still be synchronously parsing/rendering the first file; while busy it may
    ignore File -> Open Distribution Binary, creating the apparent
    "waiting for Open dialog..." stall observed with slow D&B files.

    Recovery sequence:
      1) hybrid-fast normal attempt;
      2) extra load-only grace period, without submitting/opening again;
      3) only if that also fails: clean dialogs and retry stable V6.9 OPEN.
    """
    if strategy == "stable":
        result = open_cf2_in_viewer(
            win,
            path,
            title_re,
            max(float(open_timeout), float(retry_open_timeout)),
            open_wait,
            poll_interval,
            open_trigger_mode=open_trigger_mode,
        )
        return _v6102_finalize_loaded_result(
            result,
            path,
            interactive_timeout,
        )

    if strategy != "hybrid-fast":
        raise ValueError(f"Unknown V6.10.1 open strategy: {strategy}")

    try:
        result = open_cf2_hybrid_fast_v610(
            win,
            path,
            title_re,
            open_timeout,
            open_wait,
        )
        return _v6102_finalize_loaded_result(
            result,
            path,
            interactive_timeout,
        )

    except Exception as exc:
        if not fallback:
            raise

        print(
            "    WARNING: V6.10.1 hybrid-fast OPEN did not finish in the "
            f"normal window: {exc}"
        )

        # --------------------------------------------------------------
        # Critical V6.10.1 fix:
        # the CF2 was already submitted. Give the Viewer time to finish
        # that existing transaction BEFORE attempting to open it again.
        # --------------------------------------------------------------
        if float(slow_open_grace) > 0:
            print(
                "    V6.10.4 slow-open recovery: "
                f"waiting up to {float(slow_open_grace):.1f}s "
                "for the already-submitted CF2; no second Open is triggered."
            )

            try:
                recovered = _v610_hybrid_wait_loaded(
                    win,
                    path.resolve(),
                    title_re,
                    timeout=float(slow_open_grace),
                    post_success_wait=float(open_wait),
                    poll=0.05,
                    fallback_after=2.0,
                )

                print(
                    "    V6.10.4 slow-open recovery SUCCESS: "
                    "the original Open transaction eventually completed."
                )

                return _v6102_finalize_loaded_result(
                    recovered,
                    path,
                    interactive_timeout,
                )

            except Exception as recovery_exc:
                print(
                    "    V6.10.4 slow-open recovery exhausted: "
                    f"{recovery_exc}"
                )

        print(
            "    V6.10.4 falling back to a fresh stable V6.9 "
            f"transaction (timeout={float(retry_open_timeout):.1f}s)."
        )

        # At this point the original load had both its normal timeout and the
        # extra grace period. Now it is reasonable to clean leftovers and
        # attempt a fresh stable transaction.
        try:
            _cleanup_open_dialog_fast()
        except Exception:
            pass

        try:
            _v610_native_clear_stale_errors(int(win.handle))
        except Exception:
            pass

        try:
            _, recovered_win = connect_viewer(title_re)
            win = recovered_win
        except Exception:
            pass

        # Small recovery settle so a late modal dismissal/focus transition
        # does not race the next File-menu command.
        time.sleep(0.25)

        result = open_cf2_in_viewer(
            win,
            path,
            title_re,
            max(
                float(open_timeout),
                float(retry_open_timeout),
            ),
            open_wait,
            poll_interval,
            open_trigger_mode="win32",
        )
        return _v6102_finalize_loaded_result(
            result,
            path,
            interactive_timeout,
        )


class _V610NativeCtrl:
    def __init__(self, hwnd):
        self.handle = int(hwnd)

    def rectangle(self):
        r = win32gui.GetWindowRect(self.handle)

        class _Rect:
            def __init__(self, values):
                self.left = int(values[0])
                self.top = int(values[1])
                self.right = int(values[2])
                self.bottom = int(values[3])

            def width(self):
                return self.right - self.left

            def height(self):
                return self.bottom - self.top

        return _Rect(r)


def _v610_hwnd_record(hwnd):
    try:
        rect = tuple(int(x) for x in win32gui.GetWindowRect(int(hwnd)))
    except Exception:
        rect = (0, 0, 0, 0)

    try:
        text = (win32gui.GetWindowText(int(hwnd)) or "").strip()
    except Exception:
        text = ""

    try:
        cls = win32gui.GetClassName(int(hwnd)) or ""
    except Exception:
        cls = ""

    try:
        enabled = bool(win32gui.IsWindowEnabled(int(hwnd)))
    except Exception:
        enabled = False

    try:
        visible = bool(win32gui.IsWindowVisible(int(hwnd)))
    except Exception:
        visible = False

    return {
        "hwnd": int(hwnd),
        "text": text,
        "class": cls,
        "rect": rect,
        "enabled": enabled,
        "visible": visible,
    }


def _v610_child_inventory(root_hwnd):
    return [
        _v610_hwnd_record(hwnd)
        for hwnd in _enum_descendant_hwnds_fast(int(root_hwnd))
    ]


def _v610_relative_center(item, main_rect):
    l, t, r, b = item["rect"]
    ml, mt, mr, mb = main_rect
    ww = max(1, mr - ml)
    wh = max(1, mb - mt)
    cx = (l + r) / 2.0
    cy = (t + b) / 2.0
    return (cx - ml) / ww, (cy - mt) / wh


def _v610_cabinet_submodes(main_hwnd):
    wanted = {
        "rectangular",
        "trapezoidal",
        "edges",
        "faces+edges",
        "dxf",
    }
    rows = []

    for item in _v610_child_inventory(main_hwnd):
        low = item["text"].replace("&", "").strip().lower()
        if low in wanted:
            rows.append(item)

    return rows


def _v610_find_top_cabinet_native(main_hwnd):
    main_rect = win32gui.GetWindowRect(int(main_hwnd))
    candidates = []

    for item in _v610_child_inventory(main_hwnd):
        if not (item["visible"] and item["enabled"]):
            continue

        low = item["text"].replace("&", "").strip().lower()
        if "cabinet 3d representation" not in low:
            continue

        rx, ry = _v610_relative_center(item, main_rect)

        if ry > 0.24 or rx < 0.45:
            continue

        score = 0
        if low == "cabinet 3d representation":
            score += 100
        if "button" in (item["class"] or "").lower():
            score += 30
        score -= abs(ry - 0.10) * 100

        candidates.append((score, item, rx, ry))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0]


def _v610_click_top_cabinet_native(win):
    main_hwnd = int(win.handle)
    main_rect = win32gui.GetWindowRect(main_hwnd)
    found = _v610_find_top_cabinet_native(main_hwnd)

    try:
        win.set_focus()
    except Exception:
        pass

    if found is not None:
        _, item, rx, ry = found
        l, t, r, b = item["rect"]
        x = int(l + max(4, min(18, (r - l) * 0.15)))
        y = int((t + b) / 2)
        mouse.click(button="left", coords=(x, y))
        return {
            "method": "native-text-geometry",
            "hwnd": int(item["hwnd"]),
            "click": (x, y),
            "rx": rx,
            "ry": ry,
        }

    # Conservative native-window ratio fallback. If this ever misses, the
    # production wrapper falls back to the stable UIA path.
    ml, mt, mr, mb = main_rect
    x = int(ml + 0.70 * (mr - ml))
    y = int(mt + 0.10 * (mb - mt))
    mouse.click(button="left", coords=(x, y))

    return {
        "method": "ratio-fallback",
        "hwnd": None,
        "click": (x, y),
    }


def _v610_wait_native_cabinet_verified(main_hwnd, timeout=8.0, poll=0.04):
    started = time.perf_counter()
    deadline = started + float(timeout)
    polls = 0
    last_checked = None
    last_submodes = []

    while time.perf_counter() < deadline:
        polls += 1
        found = _v610_find_top_cabinet_native(main_hwnd)
        checked = None

        if found is not None:
            _, item, _, _ = found
            checked = _radio_checked_win32(int(item["hwnd"]))

        last_checked = checked
        submodes = _v610_cabinet_submodes(main_hwnd)
        last_submodes = submodes

        enabled_submodes = [
            x for x in submodes
            if x.get("visible") and x.get("enabled")
        ]

        # Strict production rule:
        # - if BM_GETCHECK explicitly says checked, Cabinet is verified;
        # - only use enabled Cabinet submodes as fallback evidence when the
        #   native radio state is genuinely unavailable (checked is None).
        #
        # Do NOT accept enabled submodes when BM_GETCHECK explicitly reports
        # False. Some CF2 keep cabinet submode controls visible/enabled even
        # while the upper panel is still "3D balloon"; accepting them caused
        # false READY positives in V6.10.
        if checked is True or (checked is None and enabled_submodes):
            return {
                "seconds": time.perf_counter() - started,
                "polls": polls,
                "checked": checked,
                "enabled_submodes": [x.get("text") for x in enabled_submodes],
            }

        if poll > 0:
            time.sleep(float(poll))

    raise RuntimeError(
        "V6.10 Cabinet native verification timeout; "
        f"checked={last_checked}, submodes={last_submodes}"
    )


def _v610_native_scrollbar_candidates(main_hwnd):
    main_rect = win32gui.GetWindowRect(int(main_hwnd))
    out = []

    for hwnd in _enum_descendant_hwnds_fast(int(main_hwnd)):
        try:
            rect = win32gui.GetWindowRect(hwnd)
            width = rect[2] - rect[0]
            height = rect[3] - rect[1]

            if width <= 0 or height <= 0:
                continue

            cx = (rect[0] + rect[2]) / 2.0
            cy = (rect[1] + rect[3]) / 2.0
            ww = max(1, main_rect[2] - main_rect[0])
            wh = max(1, main_rect[3] - main_rect[1])
            rx = (cx - main_rect[0]) / ww
            ry = (cy - main_rect[1]) / wh

            if rx < 0.48 or ry < 0.40:
                continue

            ctrl = _V610NativeCtrl(hwnd)

            try:
                info = get_scroll_info_win32(ctrl)
            except Exception:
                continue

            if info["max"] <= info["min"]:
                continue

            orientation = None
            if width >= max(30, 2.5 * height):
                orientation = "horizontal"
            elif height >= max(30, 2.5 * width):
                orientation = "vertical"

            if orientation is None:
                continue

            out.append({
                "hwnd": int(hwnd),
                "rect": tuple(int(x) for x in rect),
                "orientation": orientation,
                "info": info,
                "rx": rx,
                "ry": ry,
            })

        except Exception:
            continue

    return out


def _v610_find_balloon_scrollbars_native(main_hwnd, h_max=71, v_max=36):
    candidates = _v610_native_scrollbar_candidates(main_hwnd)

    h_candidates = [
        item for item in candidates
        if (
            item["orientation"] == "horizontal"
            and item["info"]["min"] == 0
            and item["info"]["max"] == int(h_max)
        )
    ]

    v_candidates = [
        item for item in candidates
        if (
            item["orientation"] == "vertical"
            and item["info"]["min"] == 0
            and item["info"]["max"] == int(v_max)
        )
    ]

    h_candidates.sort(key=lambda item: item["ry"], reverse=True)
    v_candidates.sort(key=lambda item: item["rx"])

    if not h_candidates or not v_candidates:
        return None, None, candidates

    return h_candidates[0], v_candidates[0], candidates


def _v610_wait_balloon_scrollbars_native(
    main_hwnd,
    timeout=20.0,
    poll=0.04,
    h_max=71,
    v_max=36,
):
    started = time.perf_counter()
    deadline = started + float(timeout)
    polls = 0
    last_candidates = []

    while time.perf_counter() < deadline:
        polls += 1
        h, v, candidates = _v610_find_balloon_scrollbars_native(
            main_hwnd,
            h_max=h_max,
            v_max=v_max,
        )
        last_candidates = candidates

        if h is not None and v is not None:
            return (
                _V610NativeCtrl(h["hwnd"]),
                _V610NativeCtrl(v["hwnd"]),
                {
                    "seconds": time.perf_counter() - started,
                    "polls": polls,
                    "candidate_count": len(candidates),
                },
            )

        if poll > 0:
            time.sleep(float(poll))

    raise RuntimeError(
        "V6.10 native Balloon scrollbar timeout; "
        f"last_candidates={last_candidates}"
    )


def _v610_functional_ready_probe(
    h_scroll,
    v_scroll,
    visual_timeout=5.0,
    visual_poll=0.02,
):
    """One-neighbor move + visible-label verification + exact restoration."""
    started = time.perf_counter()

    h_before = get_scroll_info_win32(h_scroll)
    v_before = get_scroll_info_win32(v_scroll)

    if h_before["max"] <= h_before["min"]:
        raise RuntimeError(f"Invalid H range for readiness probe: {h_before}")

    graph_bbox, angle_bbox = derive_balloon_bboxes_from_scrollbars(
        h_scroll,
        v_scroll,
    )

    angle_ctx = PersistentGDICapture(angle_bbox)
    graph_ctx = PersistentGDICapture(graph_bbox)

    try:
        before_angle = gdi_signature(angle_bbox, angle_ctx)
        before_graph = hash(graph_ctx.grab_raw())

        original = int(h_before["pos"])
        target = original + 1 if original < h_before["max"] else original - 1

        set_scroll_position_win32(
            h_scroll,
            "horizontal",
            target,
            retries=1,
        )

        actual = get_scroll_info_win32(h_scroll)["pos"]
        if actual != target:
            raise RuntimeError(
                f"V6.10 READY probe numeric mismatch outward: "
                f"target={target}, actual={actual}"
            )

        after_angle = wait_visual_signature_change(
            angle_bbox,
            before_angle,
            timeout=float(visual_timeout),
            poll_interval=float(visual_poll),
            capture_ctx=angle_ctx,
        )
        after_graph = hash(graph_ctx.grab_raw())

        set_scroll_position_win32(
            h_scroll,
            "horizontal",
            original,
            retries=1,
        )

        restored = get_scroll_info_win32(h_scroll)["pos"]
        if restored != original:
            raise RuntimeError(
                f"V6.10 READY probe restore mismatch: "
                f"target={original}, actual={restored}"
            )

        restored_angle = wait_visual_signature_change(
            angle_bbox,
            after_angle,
            timeout=float(visual_timeout),
            poll_interval=float(visual_poll),
            capture_ctx=angle_ctx,
        )
        restored_graph = hash(graph_ctx.grab_raw())

        if restored_angle != before_angle:
            raise RuntimeError(
                "V6.10 READY probe restored numeric position but visual "
                "angle label did not return to the original signature."
            )

        return {
            "seconds": time.perf_counter() - started,
            "h_original": original,
            "h_target": target,
            "h_restored": restored,
            "v_original": int(v_before["pos"]),
            "angle_changed_out": after_angle != before_angle,
            "angle_changed_back": restored_angle != after_angle,
            "angle_restored_exact": restored_angle == before_angle,
            "graph_changed_out": after_graph != before_graph,
            "graph_changed_back": restored_graph != after_graph,
            "graph_restored_exact": restored_graph == before_graph,
        }

    finally:
        angle_ctx.close()
        graph_ctx.close()


def prepare_capture_ready_v610(
    win,
    *,
    upper_mode="cabinet",
    sft_view="T",
    ready_strategy="native",
    fallback=True,
    cabinet_timeout=8.0,
    scrollbar_timeout=20.0,
    ready_probe="functional",
    ready_probe_timeout=5.0,
):
    """Return verified H/V controls and diagnostics for production capture."""
    started = time.perf_counter()

    if ready_strategy == "stable" or upper_mode != "cabinet":
        mode_info = ensure_capture_modes_fast(
            win,
            upper_mode=upper_mode,
            settle=0.12,
        )
        sft_info = ensure_sft_view(win, sft_view, settle=0.12)
        h_scroll, v_scroll = find_balloon_scrollbars(win)

        if h_scroll is None or v_scroll is None:
            raise RuntimeError("Stable READY path could not find both scrollbars.")

        probe_info = None
        if ready_probe == "functional":
            probe_info = _v610_functional_ready_probe(
                h_scroll,
                v_scroll,
                visual_timeout=ready_probe_timeout,
                visual_poll=0.02,
            )

        return {
            "strategy": "stable",
            "h_scroll": h_scroll,
            "v_scroll": v_scroll,
            "mode_info": mode_info,
            "sft_info": sft_info,
            "probe_info": probe_info,
            "seconds": time.perf_counter() - started,
        }

    if ready_strategy != "native":
        raise ValueError(f"Unknown V6.10 ready strategy: {ready_strategy}")

    try:
        main_hwnd = int(win.handle)

        t0 = time.perf_counter()

        upper_click = {
            "method": "not-attempted",
            "hwnd": None,
            "click": None,
        }

        upper_verify = {
            "checked": None,
            "enabled_submodes": [],
            "verified": False,
        }

        upper_error = None

        # Cabinet is desirable because it minimizes upper-panel redraw cost,
        # but it is not part of Balloon-spectra data correctness. Try the
        # native route several times now that the Viewer has passed the
        # interactive gate. Do not fall into a long UIA search solely because
        # one unusual CF2 refuses the upper-view change.
        for cabinet_attempt in range(1, 4):
            try:
                upper_click = _v610_click_top_cabinet_native(win)

                upper_verify = _v610_wait_native_cabinet_verified(
                    main_hwnd,
                    timeout=min(
                        max(0.8, float(cabinet_timeout) / 3.0),
                        3.0,
                    ),
                    poll=0.04,
                )

                upper_verify["verified"] = True
                upper_error = None
                break

            except Exception as exc:
                upper_error = repr(exc)

                print(
                    "    V6.10.2 Cabinet retry "
                    f"{cabinet_attempt}/3: {exc}"
                )

                time.sleep(0.15)

        upper_seconds = time.perf_counter() - t0

        if not upper_verify.get("verified"):
            print(
                "    WARNING: V6.10.2 Cabinet could not be verified; "
                "continuing in BEST-EFFORT upper-view mode. "
                "Balloon H/V + functional probe remain strict."
            )

        balloon = _ensure_radio_selected_fast(
            win,
            "Balloon-spectra",
            settle=0.04,
        )

        if not balloon["verified"]:
            raise RuntimeError(
                "V6.10 native READY could not verify Balloon-spectra."
            )

        try:
            sft_info = ensure_sft_view(
                win,
                sft_view,
                settle=0.04,
            )
        except Exception as exc:
            # S/F/T changes only the upper representation. Treat it like
            # Cabinet: useful for performance/reproducibility, but do not
            # discard a valid Balloon capture solely because this cosmetic
            # upper-view preset is unavailable for one CF2.
            print(
                "    WARNING: V6.10.2 could not set S/F/T "
                f"view '{sft_view}': {exc}; continuing."
            )

            sft_info = {
                "view": f"{sft_view}-unverified",
                "changed": False,
                "seconds": 0.0,
                "error": repr(exc),
            }

        h_scroll, v_scroll, scroll_diag = _v610_wait_balloon_scrollbars_native(
            main_hwnd,
            timeout=scrollbar_timeout,
            poll=0.04,
            h_max=71,
            v_max=36,
        )

        probe_info = None
        if ready_probe == "functional":
            probe_info = _v610_functional_ready_probe(
                h_scroll,
                v_scroll,
                visual_timeout=ready_probe_timeout,
                visual_poll=0.02,
            )

        seconds = time.perf_counter() - started

        print(
            "    V6.10.2 READY native: "
            f"total={seconds:.3f}s | "
            f"upper={upper_seconds:.3f}s ({upper_click['method']}) | "
            f"balloon={balloon['seconds']:.3f}s | "
            f"T={sft_info['seconds']:.3f}s | "
            f"scrollbars={scroll_diag['seconds']:.3f}s "
            f"(polls={scroll_diag['polls']})"
        )

        if probe_info is not None:
            print(
                "    V6.10.2 READY functional probe: "
                f"{probe_info['seconds']:.3f}s | "
                f"H {probe_info['h_original']} -> "
                f"{probe_info['h_target']} -> "
                f"{probe_info['h_restored']} | "
                f"angle_restore={probe_info['angle_restored_exact']} | "
                f"graph_changed={probe_info['graph_changed_out']}"
            )

        mode_info = {
            "upper_changed": bool(upper_verify.get("verified")),
            "balloon_changed": bool(balloon.get("changed")),
            "upper_verified": bool(upper_verify.get("verified")),
            "balloon_verified": bool(balloon.get("verified")),
            "upper_method": (
                upper_click["method"]
                if upper_verify.get("verified")
                else "best-effort-unverified"
            ),
            "balloon_method": balloon.get("method"),
            "seconds": upper_seconds + float(balloon.get("seconds", 0.0)),
            "cabinet_checked": upper_verify.get("checked"),
            "cabinet_submodes": upper_verify.get("enabled_submodes"),
            "cabinet_error": upper_error,
        }

        return {
            "strategy": "native",
            "h_scroll": h_scroll,
            "v_scroll": v_scroll,
            "mode_info": mode_info,
            "sft_info": sft_info,
            "probe_info": probe_info,
            "seconds": seconds,
        }

    except Exception as exc:
        if not fallback:
            raise

        print(
            "    WARNING: V6.10 native READY failed; "
            f"falling back to stable V6.9 READY: {exc}"
        )

        # V6.10.2 fallback targets the data-bearing Balloon panel only.
        # Re-running an expensive UIA Cabinet search here can stall rare CF2
        # models even though Balloon itself is usable.
        balloon_fb = _ensure_radio_selected_fast(
            win,
            "Balloon-spectra",
            settle=0.12,
        )

        if not balloon_fb.get("verified"):
            raise RuntimeError(
                "V6.10.2 stable fallback could not verify Balloon-spectra."
            )

        try:
            sft_info = ensure_sft_view(
                win,
                sft_view,
                settle=0.12,
            )
        except Exception as sft_exc:
            print(
                "    WARNING: stable fallback could not set "
                f"S/F/T '{sft_view}': {sft_exc}; continuing."
            )
            sft_info = {
                "view": f"{sft_view}-unverified",
                "changed": False,
                "seconds": 0.0,
                "error": repr(sft_exc),
            }

        mode_info = {
            "upper_changed": False,
            "balloon_changed": bool(balloon_fb.get("changed")),
            "upper_verified": False,
            "balloon_verified": True,
            "upper_method": "best-effort-skipped-in-fallback",
            "balloon_method": balloon_fb.get("method"),
            "seconds": float(balloon_fb.get("seconds", 0.0)),
        }

        h_scroll, v_scroll = find_balloon_scrollbars(win)

        if h_scroll is None or v_scroll is None:
            raise RuntimeError(
                "V6.10.2 stable fallback could not find both Balloon scrollbars."
            )

        probe_info = None
        if ready_probe == "functional":
            probe_info = _v610_functional_ready_probe(
                h_scroll,
                v_scroll,
                visual_timeout=ready_probe_timeout,
                visual_poll=0.02,
            )

        seconds = time.perf_counter() - started

        print(
            "    V6.10 READY fallback-stable: "
            f"total={seconds:.3f}s"
        )

        return {
            "strategy": "fallback-stable",
            "h_scroll": h_scroll,
            "v_scroll": v_scroll,
            "mode_info": mode_info,
            "sft_info": sft_info,
            "probe_info": probe_info,
            "seconds": seconds,
        }


def main():
    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--cf2-dir",
        type=Path,
        required=True,
    )

    ap.add_argument(
        "--output-dir",
        type=Path,
        required=True,
    )

    ap.add_argument(
        "--title-re",
        default=r".*CLF reader/viewer.*",
    )

    ap.add_argument(
        "--recursive",
        action="store_true",
    )

    ap.add_argument(
        "--limit",
        type=int,
        default=None,
    )

    ap.add_argument(
        "--resume",
        action="store_true",
    )

    ap.add_argument(
        "--open-timeout",
        type=float,
        default=15.0,
    )

    ap.add_argument(
        "--open-poll-interval",
        type=float,
        default=0.08,
    )

    ap.add_argument(
        "--open-wait",
        type=float,
        default=0.08,
    )

    ap.add_argument(
        "--slow-open-grace",
        type=float,
        default=30.0,
        help=(
            "V6.10.1: extra load-only recovery window after a hybrid-fast "
            "load timeout. During this grace period the already-submitted CF2 "
            "is allowed to finish; no second Open transaction is triggered."
        ),
    )

    ap.add_argument(
        "--retry-open-timeout",
        type=float,
        default=60.0,
        help=(
            "V6.10.1: timeout for the fresh stable V6.9 Open transaction "
            "used only after normal hybrid timeout + slow-open grace fail."
        ),
    )

    ap.add_argument(
        "--viewer-interactive-timeout",
        type=float,
        default=45.0,
        help=(
            "V6.10.2: after the CF2 title is confirmed, wait natively until "
            "the Viewer message loop and Cabinet/T/Balloon controls are "
            "responsive before READY clicks are attempted."
        ),
    )

    ap.add_argument(
        "--open-trigger",
        choices=[
            "uia",
            "win32",
        ],
        default="uia",
        help=(
            "How to trigger only File -> Open Distribution Binary. "
            "'uia' preserves the V6.4 baseline. 'win32' uses the same menu "
            "path on the same HWND through pywinauto backend=win32; benchmark "
            "V2 measured ~2.87s total-to-dialog vs ~45.47s median for UIA. "
            "All subsequent open-transaction logic remains unchanged."
        ),
    )

    ap.add_argument(
        "--open-strategy",
        choices=[
            "hybrid-fast",
            "stable",
        ],
        default="hybrid-fast",
        help=(
            "V6.10 production OPEN path. hybrid-fast uses native dialog "
            "prechecks + keyboard filename submit + direct title verification. "
            "stable preserves the V6.9 transaction."
        ),
    )

    ap.add_argument(
        "--ready-strategy",
        choices=[
            "native",
            "stable",
        ],
        default="native",
        help=(
            "V6.10 READY path. native uses Win32 Cabinet selection and "
            "GetScrollInfo scrollbar discovery validated by V8."
        ),
    )

    ap.add_argument(
        "--ready-probe",
        choices=[
            "functional",
            "off",
        ],
        default="functional",
        help=(
            "functional performs one H neighbor move and exact restoration "
            "before the sweep, validating numeric position + visible label."
        ),
    )

    ap.add_argument(
        "--ready-probe-timeout",
        type=float,
        default=5.0,
    )

    ap.add_argument(
        "--native-cabinet-timeout",
        type=float,
        default=8.0,
    )

    ap.add_argument(
        "--native-scrollbar-timeout",
        type=float,
        default=20.0,
    )

    ap.add_argument(
        "--no-optimized-fallback",
        action="store_true",
        help=(
            "Disable automatic fallback to the stable V6.9 OPEN/READY paths "
            "if an optimized V6.10 step fails. Default keeps fallback enabled."
        ),
    )

    ap.add_argument(
        "--view-settle",
        type=float,
        default=0.12,
    )

    ap.add_argument(
        "--viewer-size",
        nargs=2,
        type=int,
        metavar=("WIDTH", "HEIGHT"),
        default=None,
        help=(
            "Deprecated compatibility option. V6.1 preserves the Viewer "
            "window geometry because resizing CLF Viewer can clip/reflow "
            "controls and change rendering cost."
        ),
    )

    ap.add_argument(
        "--crop-mode",
        choices=["auto", "ratios"],
        default="auto",
    )

    ap.add_argument(
        "--local-input-cache",
        type=Path,
        default=None,
        help="Optional local Windows cache, e.g. C:\\CLF_BATCH_CACHE.",
    )

    ap.add_argument(
        "--local-output-cache",
        type=Path,
        default=None,
        help=(
            "Optional local Windows output cache, e.g. C:\\CLF_OUTPUT_CACHE. "
            "New PNGs are written there during the sweep, then synchronized "
            "to --output-dir when the model finishes."
        ),
    )

    ap.add_argument(
        "--keep-local-output-cache",
        action="store_true",
        help=(
            "Keep local model PNGs after successful synchronization. "
            "Default: remove local model cache after _COMPLETE.txt."
        ),
    )

    ap.add_argument(
        "--sync-workers",
        type=int,
        default=4,
        help=(
            "Parallel workers used only when synchronizing local output "
            "cache to /shared. Start with 4; try 8 if the shared filesystem "
            "is latency-bound. Default: 4."
        ),
    )

    ap.add_argument(
        "--upper-render-mode",
        choices=[
            "cabinet",
            "3d",
        ],
        default="cabinet",
        help=(
            "cabinet evita el redibujado costoso del 3D balloon. "
            "Default: cabinet."
        ),
    )

    ap.add_argument(
        "--sft-view",
        choices=[
            "keep",
            "S",
            "F",
            "T",
        ],
        default="T",
        help=(
            "Upper-render S/F/T preset. Benchmark over 300 states found "
            "T fastest with zero graph/angle mismatches versus S/F."
        ),
    )

    ap.add_argument(
        "--capture-payload",
        choices=[
            "pil-main",
            "raw-worker",
        ],
        default="raw-worker",
        help=(
            "pil-main preserves V6.7. raw-worker queues immutable BGRX bytes "
            "and moves PIL construction + PNG encoding to save workers."
        ),
    )

    ap.add_argument(
        "--capture-storage",
        choices=[
            "files",
            "row-zip",
        ],
        default="row-zip",
        help=(
            "files preserves V6.8 individual PNG output. row-zip writes "
            "one ZIP_STORED archive per vertical row."
        ),
    )

    ap.add_argument(
        "--row-zip-max-pending",
        type=int,
        default=2,
        help=(
            "Maximum row-ZIP write jobs in flight. Default 2 bounds memory "
            "while overlapping ZIP I/O with the next row."
        ),
    )

    ap.add_argument(
        "--fixed-grid",
        nargs=2,
        type=int,
        metavar=(
            "H",
            "V",
        ),
        default=[
            72,
            37,
        ],
    )

    ap.add_argument(
        "--move-timeout",
        type=float,
        default=1.5,
    )

    ap.add_argument(
        "--move-poll-interval",
        type=float,
        default=0.001,
    )

    ap.add_argument(
        "--visual-timeout",
        type=float,
        default=1.20,
        help=(
            "Timeout para confirmar mediante GDI que cambió "
            "el label angular."
        ),
    )

    ap.add_argument(
        "--visual-poll-interval",
        type=float,
        default=0.002,
    )

    ap.add_argument(
        "--post-visual-settle",
        type=float,
        default=0.003,
        help=(
            "Pequeña estabilización después de confirmar visualmente "
            "el nuevo ángulo y antes de capturar la curva."
        ),
    )

    ap.add_argument(
        "--verification-mode",
        choices=[
            "strict",
            "hybrid-row",
        ],
        default="strict",
        help=(
            "strict verifica el label angular en cada movimiento. "
            "hybrid-row conserva GetScrollInfo en cada movimiento y verifica "
            "visualmente sólo transiciones verticales y extremos de fila. "
            "El modo hybrid-row fue validado contra STRICT en un grid completo "
            "72x37 antes de incorporarse aquí."
        ),
    )

    ap.add_argument(
        "--hotpath-scroll-reader",
        choices=[
            "getscrollpos",
            "sif-all",
        ],
        default="getscrollpos",
        help=(
            "Reader del hot path. getscrollpos fue validado con 0 mismatches "
            "y menor latencia; sif-all conserva el comportamiento V6.6."
        ),
    )

    ap.add_argument(
        "--gdi-capture-mode",
        choices=[
            "persistent",
            "legacy",
        ],
        default="persistent",
        help=(
            "persistent reutiliza DC/bitmap para los ROI fijos. "
            "legacy recrea el contexto GDI en cada captura como V6.6."
        ),
    )

    ap.add_argument(
        "--save-angle-labels",
        choices=[
            "all",
            "audit",
            "off",
        ],
        default="audit",
    )

    ap.add_argument(
        "--audit-every",
        type=int,
        default=100,
    )

    ap.add_argument(
        "--png-compress-level",
        type=int,
        default=0,
        choices=range(
            0,
            10,
        ),
        metavar="0..9",
    )

    ap.add_argument(
        "--save-workers",
        type=int,
        default=3,
        help=(
            "PNG save workers. V6.8 defaults to 3 for the current 3-CPU VM."
        ),
    )

    ap.add_argument(
        "--max-pending-saves",
        type=int,
        default=16,
    )

    ap.add_argument(
        "--capture-ratios",
        nargs=4,
        type=float,
        default=[
            0.566,
            0.533,
            0.985,
            0.936,
        ],
        metavar=(
            "X0",
            "Y0",
            "X1",
            "Y1",
        ),
    )

    ap.add_argument(
        "--angle-label-ratios",
        nargs=4,
        type=float,
        default=[
            0.565,
            0.507,
            0.690,
            0.565,
        ],
        metavar=(
            "X0",
            "Y0",
            "X1",
            "Y1",
        ),
    )

    ap.add_argument(
        "--distributed",
        action="store_true",
        help=(
            "Coordena múltiples workers mediante locks atómicos en el output compartido. "
            "Usar junto con --resume."
        ),
    )

    ap.add_argument(
        "--worker-id",
        default=None,
        help=(
            "Identificador único del worker. Default: hostname-pid. "
            "Ejemplos: win7-a, win7-b."
        ),
    )

    ap.add_argument(
        "--claim-timeout-min",
        type=float,
        default=30.0,
        help="Un claim sin heartbeat por este tiempo se considera abandonado.",
    )

    ap.add_argument(
        "--claim-heartbeat-sec",
        type=float,
        default=10.0,
        help="Frecuencia de heartbeat del claim activo.",
    )

    ap.add_argument(
        "--multipass-wait-sec",
        type=float,
        default=10.0,
        help=(
            "En modo distribuido, espera entre pasadas cuando todavía "
            "quedan modelos pendientes. Default: 10 s."
        ),
    )

    ap.add_argument(
        "--max-idle-passes",
        type=int,
        default=3,
        help=(
            "Detiene el worker después de N pasadas sin progreso global "
            "y sin claims activos. Evita loops infinitos con CF2 que fallan "
            "permanentemente. Default: 3."
        ),
    )

    ap.add_argument(
        "--single-pass",
        action="store_true",
        help=(
            "Desactiva el scheduler multipass. Útil sólo para diagnóstico. "
            "Por defecto --distributed continúa hasta completar todo o "
            "alcanzar --max-idle-passes."
        ),
    )

    ap.add_argument(
        "--state-limit",
        type=int,
        default=None,
        help=(
            "Benchmark: limita estados procesados; "
            "no escribe _COMPLETE.txt."
        ),
    )

    args = ap.parse_args()

    configure_hotpath_scroll_reader(
        args.hotpath_scroll_reader
    )

    if (
        args.capture_payload == "raw-worker"
        and
        args.gdi_capture_mode != "persistent"
    ):
        raise RuntimeError(
            "--capture-payload raw-worker requires "
            "--gdi-capture-mode persistent."
        )

    if (
        args.capture_storage == "row-zip"
        and
        args.capture_payload != "raw-worker"
    ):
        raise RuntimeError(
            "--capture-storage row-zip currently requires "
            "--capture-payload raw-worker."
        )

    if (
        args.capture_storage == "row-zip"
        and
        args.local_output_cache is not None
    ):
        raise RuntimeError(
            "--capture-storage row-zip writes directly to shared output; "
            "do not combine it with --local-output-cache."
        )

    args.output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    if args.distributed and not args.resume:
        raise RuntimeError("--distributed requiere --resume para evitar sobrescribir modelos completos.")

    worker_id = args.worker_id or f"{socket.gethostname()}-{os.getpid()}"
    worker_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", worker_id)
    claims_dir = args.output_dir / "_claims"


    pattern = (
        "**/*.CF2"
        if args.recursive
        else "*.CF2"
    )

    files = list(
        args.cf2_dir.glob(
            pattern
        )
    )

    pattern2 = (
        "**/*.cf2"
        if args.recursive
        else "*.cf2"
    )

    known = {
        str(
            p.resolve()
        ).lower()
        for p in files
    }

    for p in args.cf2_dir.glob(
        pattern2
    ):
        if str(
            p.resolve()
        ).lower() not in known:
            files.append(
                p
            )

    files = sorted(
        files,
        key=lambda p: p.name.lower(),
    )

    if args.limit is not None:
        files = files[
            :args.limit
        ]

    if not files:
        raise RuntimeError(
            f"No encontré CF2 en {args.cf2_dir}"
        )

    backend, win = connect_viewer(
        args.title_re
    )

    print(
        "="
        *
        100
    )

    print(
        "CLF VIEWER BALLOON-SPECTRA V6.10.4 TITLE-DIGEST + INTERACTIVE-GATE - "
        "Cabinet render + async PostMessage line + "
        "GetScrollInfo + visual GDI verification"
    )

    print(
        "="
        *
        100
    )

    print(
        "Files:",
        len(
            files
        ),
    )

    if args.limit is None:
        print(
            "Target set: ALL discovered CF2 files "
            f"({len(files)} global models; workers share this same list)"
        )
    else:
        print(
            f"Target set limited by --limit={args.limit}: "
            f"{len(files)} global models"
        )

    print(
        "Backend:",
        backend,
    )

    print(
        "V6.10.1 optimized path:",
        f"open={args.open_strategy}, "
        f"ready={args.ready_strategy}, "
        f"probe={args.ready_probe}, "
        f"fallback={'off' if args.no_optimized_fallback else 'stable-v6.9'}",
    )

    if args.distributed:
        print("Distributed worker:", worker_id)
        print("Claims dir:", claims_dir)
        print("Claim timeout:", f"{args.claim_timeout_min:.1f} min")

    print()

    batch_rows = []

    expected_h = None
    expected_v = None

    if args.fixed_grid is not None:
        expected_h = int(args.fixed_grid[0])
        expected_v = int(args.fixed_grid[1])

    pass_index = 0
    idle_passes = 0
    stale_seconds = max(
        60.0,
        args.claim_timeout_min * 60.0,
    )
    previous_complete = None

    while True:
        pass_index += 1

        print(
            f"Scanning global completion markers for {len(files)} models..."
        )

        before_summary = summarize_distributed_state(
            files,
            args.output_dir,
            claims_dir,
            stale_seconds,
            expected_h=expected_h,
            expected_v=expected_v,
            progress_every=100 if pass_index == 1 else 0,
        )

        print()
        print("=" * 100)
        print(
            f"MULTIPASS {pass_index} | worker={worker_id}"
        )
        print_global_status(
            before_summary,
            prefix="START",
        )
        print("=" * 100)

        pass_claimed = 0
        pass_completed = 0
        pass_errors = 0

        for file_index, cf2_path in enumerate(
            files,
            start=1,
        ):
            model = safe_model_name(
                cf2_path
            )

            print(
                f"[{file_index:03d}/{len(files):03d}] "
                f"{cf2_path.name}"
            )

            root = (
                args.output_dir
                /
                model
            )

            shared_capture_dir = (
                root
                /
                "captures"
            )

            shared_angle_dir = (
                root
                /
                "angle_labels"
            )

            shared_row_zip_dir = (
                root
                /
                "capture_rows"
            )

            complete_marker = (
                root
                /
                "_COMPLETE.txt"
            )

            local_model_output = prepare_local_output_dirs(
                args.local_output_cache,
                model,
            )

            if local_model_output is None:
                capture_dir = shared_capture_dir
                angle_dir = shared_angle_dir
            else:
                capture_dir = local_model_output["capture_dir"]
                angle_dir = local_model_output["angle_dir"]

            if (
                args.resume
                and
                quick_complete_state(
                    root,
                    expected_h=expected_h,
                    expected_v=expected_v,
                )
            ):
                print(
                    f"    COMPLETE marker verified: {complete_marker}"
                )

                print(
                    "    SKIP complete"
                )

                continue

            claim = None

            if args.distributed:
                claim, claim_reason = try_claim_model(
                    claims_dir=claims_dir,
                    model=model,
                    worker_id=worker_id,
                    cf2_path=cf2_path,
                    stale_seconds=max(60.0, args.claim_timeout_min * 60.0),
                    heartbeat_seconds=args.claim_heartbeat_sec,
                )

                if claim is None:
                    print(f"    SKIP claimed by another worker ({claim_reason})")
                    continue

                print(f"    CLAIMED by {worker_id}")

                pass_claimed += 1

                # A second worker may have completed the model between our first
                # precheck and acquiring the lock. Re-check cheaply under ownership.
                if quick_complete_state(
                    root,
                    expected_h=expected_h,
                    expected_v=expected_v,
                ):
                    print(
                        "    COMPLETE appeared before processing; releasing claim"
                    )
                    claim.release()
                    claim = None
                    continue

                # Only now pay for deep inspection of a partial model. This is
                # necessary for --resume repair, but no longer slows global scheduling.
                precheck_after_claim = inspect_existing_v2(
                    root
                )

            executor = None
            row_writer_executor = None
            graph_capture_ctx = None
            angle_capture_ctx = None

            try:
                preflight = preflight_cf2_file(
                    cf2_path
                )

                open_path, cache_seconds, copied_to_cache = prepare_local_cf2_copy(
                    cf2_path,
                    args.local_input_cache,
                )

                if args.local_input_cache is not None:
                    print(
                        f"    local input cache: {open_path} "
                        f"({'copied' if copied_to_cache else 'reused'}) "
                        f"in {cache_seconds:.3f}s"
                    )

                if args.viewer_size is not None:
                    print(
                        "    NOTE: --viewer-size is ignored in V6.1; "
                        "preserving native CLF Viewer window size."
                    )

                print(
                    "    opening..."
                )

                loaded_backend, loaded_win, loaded_title = open_cf2_production_v610(
                    win,
                    open_path,
                    args.title_re,
                    args.open_timeout,
                    args.open_wait,
                    args.open_poll_interval,
                    open_trigger_mode=args.open_trigger,
                    strategy=args.open_strategy,
                    fallback=not args.no_optimized_fallback,
                    slow_open_grace=args.slow_open_grace,
                    retry_open_timeout=args.retry_open_timeout,
                    interactive_timeout=args.viewer_interactive_timeout,
                )

                if loaded_backend is not None:
                    backend = loaded_backend

                win = loaded_win

                print(
                    "    viewer:",
                    loaded_title,
                )

                close_extra_clf_viewer_windows(
                    win,
                    args.title_re,
                )

                assert_loaded_cf2(
                    win,
                    open_path.resolve(),
                )

                ready_info = prepare_capture_ready_v610(
                    win,
                    upper_mode=args.upper_render_mode,
                    sft_view=args.sft_view,
                    ready_strategy=args.ready_strategy,
                    fallback=not args.no_optimized_fallback,
                    cabinet_timeout=args.native_cabinet_timeout,
                    scrollbar_timeout=args.native_scrollbar_timeout,
                    ready_probe=args.ready_probe,
                    ready_probe_timeout=args.ready_probe_timeout,
                )

                mode_info = ready_info["mode_info"]
                sft_info = ready_info["sft_info"]
                h_scroll = ready_info["h_scroll"]
                v_scroll = ready_info["v_scroll"]

                print(
                    "    capture readiness: "
                    f"strategy={ready_info['strategy']} | "
                    f"upper_method={mode_info.get('upper_method')} | "
                    f"balloon_method={mode_info.get('balloon_method')} | "
                    f"S/F/T={sft_info.get('view')} | "
                    f"total={ready_info['seconds']:.3f}s"
                )

                assert_loaded_cf2(
                    win,
                    open_path.resolve(),
                )

                h_info = get_scroll_info_win32(
                    h_scroll
                )

                v_info = get_scroll_info_win32(
                    v_scroll
                )

                horizontal_states = (
                    h_info[
                        "max"
                    ]
                    -
                    h_info[
                        "min"
                    ]
                    +
                    1
                )

                vertical_states = (
                    v_info[
                        "max"
                    ]
                    -
                    v_info[
                        "min"
                    ]
                    +
                    1
                )

                print(
                    "    GetScrollInfo horizontal:",
                    h_info,
                )

                print(
                    "    GetScrollInfo vertical:  ",
                    v_info,
                )

                print(
                    f"    angular grid: "
                    f"{horizontal_states} x {vertical_states} = "
                    f"{horizontal_states * vertical_states} states"
                )

                if args.fixed_grid is not None:
                    expected_h, expected_v = args.fixed_grid

                    if (
                        horizontal_states
                        !=
                        expected_h
                        or
                        vertical_states
                        !=
                        expected_v
                    ):
                        raise RuntimeError(
                            "Malla distinta de --fixed-grid: "
                            f"viewer={horizontal_states}x{vertical_states}, "
                            f"expected={expected_h}x{expected_v}"
                        )

                if args.crop_mode == "auto":
                    graph_bbox, angle_bbox = derive_balloon_bboxes_from_scrollbars(
                        h_scroll,
                        v_scroll,
                    )
                else:
                    graph_bbox = absolute_bbox_from_ratios(
                        win,
                        tuple(args.capture_ratios),
                    )
                    angle_bbox = absolute_bbox_from_ratios(
                        win,
                        tuple(args.angle_label_ratios),
                    )

                print(
                    f"    Balloon ROI: {graph_bbox} "
                    f"({graph_bbox[2]-graph_bbox[0]}x{graph_bbox[3]-graph_bbox[1]}px), "
                    f"crop_mode={args.crop_mode}"
                )

                gdi_context_setup_seconds = 0.0

                if args.gdi_capture_mode == "persistent":
                    t_gdi_setup = time.perf_counter()

                    graph_capture_ctx = PersistentGDICapture(
                        graph_bbox
                    )

                    angle_capture_ctx = PersistentGDICapture(
                        angle_bbox
                    )

                    gdi_context_setup_seconds = (
                        time.perf_counter()
                        -
                        t_gdi_setup
                    )

                    print(
                        f"    GDI contexts: persistent "
                        f"setup={gdi_context_setup_seconds:.3f}s"
                    )
                else:
                    print(
                        "    GDI contexts: legacy per-capture"
                    )

                # Establish visible state signature BEFORE homing.
                angle_signature = gdi_signature(
                    angle_bbox,
                    angle_capture_ctx,
                )

                home_seconds = 0.0

                angle_signature, dt = direct_position_verified_v5(
                    h_scroll,
                    "horizontal",
                    h_info["min"],
                    angle_bbox,
                    angle_signature,
                    args.visual_timeout,
                    args.visual_poll_interval,
                    args.post_visual_settle,
                    angle_capture_ctx=angle_capture_ctx,
                )

                home_seconds += dt

                angle_signature, dt = direct_position_verified_v5(
                    v_scroll,
                    "vertical",
                    v_info["min"],
                    angle_bbox,
                    angle_signature,
                    args.visual_timeout,
                    args.visual_poll_interval,
                    args.post_visual_settle,
                    angle_capture_ctx=angle_capture_ctx,
                )

                home_seconds += dt

                print(
                    f"    direct home verified: {home_seconds:.3f}s"
                )

                capture_dir.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                if args.save_angle_labels != "off":
                    angle_dir.mkdir(
                        parents=True,
                        exist_ok=True,
                    )

                shared_capture_dir.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                if args.save_angle_labels != "off":
                    shared_angle_dir.mkdir(
                        parents=True,
                        exist_ok=True,
                    )

                if args.capture_storage == "row-zip":
                    shared_row_zip_dir.mkdir(
                        parents=True,
                        exist_ok=True,
                    )

                executor = ThreadPoolExecutor(
                    max_workers=max(
                        1,
                        int(
                            args.save_workers
                        ),
                    )
                )

                pending_saves = []

                if args.capture_storage == "row-zip":
                    row_writer_executor = ThreadPoolExecutor(
                        max_workers=1
                    )

                row_buffers = {}
                pending_row_writes = []

                row_zip_submit_seconds = 0.0
                row_zip_backpressure_seconds = 0.0
                row_zip_backpressure_events = 0
                row_zip_final_drain_seconds = 0.0
                row_zip_archives_written = 0
                row_zip_new_members = 0
                row_zip_bytes_written = 0
                max_pending_row_writes = 0
                row_encode_jobs_queued = 0

                save_backpressure_seconds = 0.0
                save_backpressure_events = 0
                save_submit_seconds = 0.0
                save_final_drain_seconds = 0.0
                max_pending_observed = 0
                save_jobs_queued = 0

                def queue_png(
                    payload,
                    path,
                    *,
                    raw_size=None,
                ):
                    nonlocal save_backpressure_seconds
                    nonlocal save_backpressure_events
                    nonlocal save_submit_seconds
                    nonlocal max_pending_observed
                    nonlocal save_jobs_queued

                    t_submit = time.perf_counter()

                    if raw_size is None:
                        future = executor.submit(
                            save_png_fast,
                            payload,
                            path,
                            args.png_compress_level,
                        )
                    else:
                        raw_width, raw_height = raw_size

                        future = executor.submit(
                            save_png_raw_fast,
                            payload,
                            raw_width,
                            raw_height,
                            path,
                            args.png_compress_level,
                        )

                    pending_saves.append(future)

                    save_submit_seconds += (
                        time.perf_counter()
                        -
                        t_submit
                    )

                    save_jobs_queued += 1

                    if len(pending_saves) > max_pending_observed:
                        max_pending_observed = len(
                            pending_saves
                        )

                    limit = max(
                        1,
                        int(
                            args.max_pending_saves
                        ),
                    )

                    if len(pending_saves) >= limit:
                        oldest = pending_saves.pop(0)

                        t_wait = time.perf_counter()
                        oldest.result()

                        wait_dt = (
                            time.perf_counter()
                            -
                            t_wait
                        )

                        save_backpressure_seconds += wait_dt

                        if wait_dt >= 0.001:
                            save_backpressure_events += 1


                def queue_graph_for_row(
                    raw_bgrx,
                    width,
                    height,
                    hi,
                    vi,
                ):
                    nonlocal save_submit_seconds
                    nonlocal row_encode_jobs_queued

                    member_name = (
                        f"{model}__x{int(hi):03d}__y{int(vi):03d}"
                        f"__balloon_spectra.png"
                    )

                    t_submit = time.perf_counter()

                    future = executor.submit(
                        encode_png_raw_bytes,
                        raw_bgrx,
                        width,
                        height,
                        args.png_compress_level,
                    )

                    save_submit_seconds += (
                        time.perf_counter()
                        -
                        t_submit
                    )

                    row_encode_jobs_queued += 1

                    row_buffers.setdefault(
                        int(vi),
                        [],
                    ).append(
                        (
                            member_name,
                            future,
                        )
                    )


                def flush_capture_row(
                    vi,
                ):
                    nonlocal row_zip_submit_seconds
                    nonlocal row_zip_backpressure_seconds
                    nonlocal row_zip_backpressure_events
                    nonlocal row_zip_archives_written
                    nonlocal row_zip_new_members
                    nonlocal row_zip_bytes_written
                    nonlocal max_pending_row_writes

                    if args.capture_storage != "row-zip":
                        return

                    items = row_buffers.pop(
                        int(vi),
                        [],
                    )

                    if not items:
                        return

                    archive_path = expected_row_zip_path(
                        shared_row_zip_dir,
                        model,
                        int(vi),
                    )

                    t_submit = time.perf_counter()

                    future = row_writer_executor.submit(
                        write_or_merge_row_zip_from_futures,
                        archive_path,
                        items,
                    )

                    row_zip_submit_seconds += (
                        time.perf_counter()
                        -
                        t_submit
                    )

                    pending_row_writes.append(
                        future
                    )

                    max_pending_row_writes = max(
                        max_pending_row_writes,
                        len(
                            pending_row_writes
                        ),
                    )

                    limit = max(
                        1,
                        int(
                            args.row_zip_max_pending
                        ),
                    )

                    if len(
                        pending_row_writes
                    ) >= limit:
                        oldest = pending_row_writes.pop(
                            0
                        )

                        t_wait = time.perf_counter()

                        info = oldest.result()

                        wait_dt = (
                            time.perf_counter()
                            -
                            t_wait
                        )

                        row_zip_backpressure_seconds += wait_dt

                        if wait_dt >= 0.001:
                            row_zip_backpressure_events += 1

                        row_zip_archives_written += 1
                        row_zip_new_members += int(
                            info.get(
                                "new_members",
                                0,
                            )
                        )
                        row_zip_bytes_written += int(
                            info.get(
                                "bytes",
                                0,
                            )
                        )



                expected_total = (
                    horizontal_states
                    *
                    vertical_states
                )

                move_seconds = 0.0
                capture_seconds = 0.0
                visual_audit_seconds = 0.0
                capture_gate_seconds = 0.0
                angle_index_seconds = 0.0
                angle_lookup_seconds = 0.0
                hybrid_visual_checks = 0
                processed = 0
                captured_new = 0
                skipped_existing = 0
                state_limit_hit = False

                shared_existing_states, shared_index_seconds = scan_existing_capture_states(
                    shared_capture_dir,
                    model,
                )

                (
                    shared_zip_states,
                    shared_zip_member_map,
                    shared_zip_index_seconds,
                ) = scan_existing_row_zip_states(
                    shared_row_zip_dir,
                    model,
                )

                local_existing_states = set()
                local_index_seconds = 0.0

                if local_model_output is not None:
                    local_existing_states, local_index_seconds = scan_existing_capture_states(
                        capture_dir,
                        model,
                    )

                existing_states = (
                    shared_existing_states
                    |
                    shared_zip_states
                    |
                    local_existing_states
                )

                print(
                    f"    resume index: {len(existing_states)} state(s) "
                    f"in "
                    f"{shared_index_seconds + shared_zip_index_seconds + local_index_seconds:.3f}s "
                    f"(files={len(shared_existing_states)}, "
                    f"rowzip={len(shared_zip_states)}, "
                    f"local={len(local_existing_states)})"
                )

                # V6.6: index angle-label filenames once instead of calling
                # Path.is_file() on /shared during every audit state.
                existing_angle_names = set()
                shared_angle_names = set()
                local_angle_names = set()

                if args.save_angle_labels != "off":
                    t_angle_index = time.perf_counter()

                    shared_angle_names = scan_existing_filenames(
                        shared_angle_dir
                    )

                    if local_model_output is not None:
                        local_angle_names = scan_existing_filenames(
                            angle_dir
                        )

                    existing_angle_names = (
                        shared_angle_names
                        |
                        local_angle_names
                    )

                    angle_index_seconds = (
                        time.perf_counter()
                        -
                        t_angle_index
                    )

                    print(
                        f"    angle-label index: "
                        f"{len(existing_angle_names)} file(s) "
                        f"in {angle_index_seconds:.3f}s "
                        f"(shared={len(shared_angle_names)}, "
                        f"local={len(local_angle_names)})"
                    )

                run_t0 = time.perf_counter()

                def capture_current_state(
                    hi: int,
                    vi: int,
                ):
                    nonlocal capture_seconds
                    nonlocal visual_audit_seconds
                    nonlocal capture_gate_seconds
                    nonlocal angle_lookup_seconds
                    nonlocal processed
                    nonlocal captured_new
                    nonlocal skipped_existing

                    # Numerical safety gate immediately before capture.
                    t_gate = time.perf_counter()

                    actual_h = get_scroll_pos_hot(
                        h_scroll
                    )

                    actual_v = get_scroll_pos_hot(
                        v_scroll
                    )

                    capture_gate_seconds += (
                        time.perf_counter()
                        -
                        t_gate
                    )

                    if (
                        actual_h != hi
                        or
                        actual_v != vi
                    ):
                        raise RuntimeError(
                            "Estado angular incorrecto antes de captura: "
                            f"expected=({hi},{vi}), "
                            f"actual=({actual_h},{actual_v})"
                        )

                    capture_path = expected_capture_path(
                        capture_dir,
                        model,
                        hi,
                        vi,
                    )

                    state_key = (
                        hi,
                        vi,
                    )

                    if state_key in existing_states:
                        skipped_existing += 1
                    else:
                        t0 = time.perf_counter()

                        if args.capture_payload == "raw-worker":
                            graph = graph_capture_ctx.grab_raw()

                            graph_raw_size = (
                                graph_capture_ctx.width,
                                graph_capture_ctx.height,
                            )
                        else:
                            graph = gdi_capture_bbox(
                                graph_bbox,
                                graph_capture_ctx,
                            )

                            graph_raw_size = None

                        capture_seconds += (
                            time.perf_counter()
                            -
                            t0
                        )

                        if args.capture_storage == "row-zip":
                            if graph_raw_size is None:
                                raise RuntimeError(
                                    "row-zip requires raw-worker graph payload."
                                )

                            queue_graph_for_row(
                                graph,
                                graph_raw_size[0],
                                graph_raw_size[1],
                                hi,
                                vi,
                            )
                        else:
                            queue_png(
                                graph,
                                capture_path,
                                raw_size=graph_raw_size,
                            )

                        existing_states.add(
                            state_key
                        )

                        captured_new += 1

                    if want_audit_label(
                        args.save_angle_labels,
                        args.audit_every,
                        hi,
                        vi,
                        horizontal_states,
                        vertical_states,
                    ):
                        angle_path = expected_angle_path(
                            angle_dir,
                            model,
                            hi,
                            vi,
                        )

                        t_lookup = time.perf_counter()

                        angle_exists = (
                            angle_path.name
                            in existing_angle_names
                        )

                        angle_lookup_seconds += (
                            time.perf_counter()
                            -
                            t_lookup
                        )

                        if not angle_exists:
                            t0 = time.perf_counter()

                            angle_img = gdi_capture_bbox(
                                angle_bbox,
                                angle_capture_ctx,
                            )

                            visual_audit_seconds += (
                                time.perf_counter()
                                -
                                t0
                            )

                            queue_png(
                                angle_img,
                                angle_path,
                            )

                            # Mark as existing as soon as it is queued so this
                            # run cannot schedule the same audit image twice.
                            existing_angle_names.add(
                                angle_path.name
                            )

                    processed += 1

                # Initial state x=0,y=0.
                capture_current_state(
                    0,
                    0,
                )

                if (
                    args.state_limit is not None
                    and
                    processed >= max(
                        1,
                        args.state_limit,
                    )
                ):
                    state_limit_hit = True

                if not state_limit_hit:
                    for vi in range(
                        vertical_states
                    ):
                        if vi % 2 == 0:
                            start_hi = 0
                            end_hi = horizontal_states - 1
                            direction = +1
                        else:
                            start_hi = horizontal_states - 1
                            end_hi = 0
                            direction = -1

                        # Row 0 initial state has already been captured.
                        if vi == 0:
                            current_hi = 0
                        else:
                            current_hi = start_hi

                        while current_hi != end_hi:
                            target_hi = (
                                current_hi
                                +
                                direction
                            )

                            if args.verification_mode == "strict":
                                angle_signature, dt = step_line_verified_v5(
                                    h_scroll,
                                    "horizontal",
                                    direction,
                                    target_hi,
                                    angle_bbox,
                                    angle_signature,
                                    args.move_timeout,
                                    args.move_poll_interval,
                                    args.visual_timeout,
                                    args.visual_poll_interval,
                                    args.post_visual_settle,
                                    angle_capture_ctx=angle_capture_ctx,
                                )

                            else:
                                verify_visual = (
                                    target_hi == 0
                                    or
                                    target_hi == horizontal_states - 1
                                )

                                dt, did_visual = step_line_hybrid_row_v64(
                                    h_scroll,
                                    "horizontal",
                                    direction,
                                    target_hi,
                                    angle_bbox,
                                    verify_visual=verify_visual,
                                    move_timeout=args.move_timeout,
                                    poll_interval=args.move_poll_interval,
                                    visual_timeout=args.visual_timeout,
                                    visual_poll_interval=args.visual_poll_interval,
                                    post_visual_settle=args.post_visual_settle,
                                    angle_capture_ctx=angle_capture_ctx,
                                )

                                if did_visual:
                                    hybrid_visual_checks += 1

                            move_seconds += dt
                            current_hi = target_hi

                            capture_current_state(
                                current_hi,
                                vi,
                            )

                            if (
                                args.state_limit is not None
                                and
                                processed
                                >=
                                max(
                                    1,
                                    args.state_limit,
                                )
                            ):
                                state_limit_hit = True
                                break

                        flush_capture_row(
                            vi
                        )

                        if state_limit_hit:
                            break

                        if vi >= vertical_states - 1:
                            break

                        target_vi = (
                            vi
                            +
                            1
                        )

                        if args.verification_mode == "strict":
                            angle_signature, dt = step_line_verified_v5(
                                v_scroll,
                                "vertical",
                                +1,
                                target_vi,
                                angle_bbox,
                                angle_signature,
                                args.move_timeout,
                                args.move_poll_interval,
                                args.visual_timeout,
                                args.visual_poll_interval,
                                args.post_visual_settle,
                                angle_capture_ctx=angle_capture_ctx,
                            )

                        else:
                            dt, did_visual = step_line_hybrid_row_v64(
                                v_scroll,
                                "vertical",
                                +1,
                                target_vi,
                                angle_bbox,
                                verify_visual=True,
                                move_timeout=args.move_timeout,
                                poll_interval=args.move_poll_interval,
                                visual_timeout=args.visual_timeout,
                                visual_poll_interval=args.visual_poll_interval,
                                post_visual_settle=args.post_visual_settle,
                                angle_capture_ctx=angle_capture_ctx,
                            )

                            if did_visual:
                                hybrid_visual_checks += 1

                        move_seconds += dt

                        # Same horizontal endpoint, next vertical row.
                        capture_current_state(
                            end_hi,
                            target_vi,
                        )

                        if (
                            args.state_limit is not None
                            and
                            processed
                            >=
                            max(
                                1,
                                args.state_limit,
                            )
                        ):
                            state_limit_hit = True
                            break

                if args.capture_storage == "row-zip":
                    for pending_vi in sorted(
                        list(
                            row_buffers.keys()
                        )
                    ):
                        flush_capture_row(
                            pending_vi
                        )

                t_final_drain = time.perf_counter()

                for future in pending_saves:
                    future.result()

                save_final_drain_seconds += (
                    time.perf_counter()
                    -
                    t_final_drain
                )

                if args.capture_storage == "row-zip":
                    t_row_drain = time.perf_counter()

                    for future in pending_row_writes:
                        info = future.result()

                        row_zip_archives_written += 1
                        row_zip_new_members += int(
                            info.get(
                                "new_members",
                                0,
                            )
                        )
                        row_zip_bytes_written += int(
                            info.get(
                                "bytes",
                                0,
                            )
                        )

                    pending_row_writes.clear()

                    row_zip_final_drain_seconds += (
                        time.perf_counter()
                        -
                        t_row_drain
                    )

                    row_writer_executor.shutdown(
                        wait=True
                    )

                    row_writer_executor = None

                executor.shutdown(
                    wait=True
                )

                executor = None

                elapsed = (
                    time.perf_counter()
                    -
                    run_t0
                )

                print(
                    f"    V5 timing: {elapsed:.2f}s total, "
                    f"{processed} states, "
                    f"{processed / max(elapsed, 1e-9):.2f} states/s"
                )

                print(
                    f"    verified move time: {move_seconds:.2f}s; "
                    f"GDI graph capture: {capture_seconds:.2f}s; "
                    f"angle audit capture: {visual_audit_seconds:.2f}s"
                )

                print(
                    f"    verification mode: {args.verification_mode}; "
                    f"hybrid visual checks: {hybrid_visual_checks}"
                )

                print(
                    f"    hot path: scroll_reader={args.hotpath_scroll_reader}; "
                    f"gdi={args.gdi_capture_mode}; "
                    f"payload={args.capture_payload}; "
                    f"storage={args.capture_storage}; "
                    f"sft={args.sft_view}; "
                    f"gdi_setup={gdi_context_setup_seconds:.3f}s"
                )

                print(
                    "    I/O profile: "
                    f"capture_gate={capture_gate_seconds:.2f}s; "
                    f"save_submit={save_submit_seconds:.2f}s; "
                    f"save_backpressure={save_backpressure_seconds:.2f}s "
                    f"({save_backpressure_events} blocking events); "
                    f"final_save_drain={save_final_drain_seconds:.2f}s; "
                    f"angle_index={angle_index_seconds:.2f}s; "
                    f"angle_lookup={angle_lookup_seconds:.4f}s; "
                    f"save_jobs={save_jobs_queued}; "
                    f"max_pending={max_pending_observed}"
                )

                if args.capture_storage == "row-zip":
                    print(
                        "    row-ZIP profile: "
                        f"encode_jobs={row_encode_jobs_queued}; "
                        f"submit={row_zip_submit_seconds:.2f}s; "
                        f"backpressure={row_zip_backpressure_seconds:.2f}s "
                        f"({row_zip_backpressure_events} blocking events); "
                        f"final_drain={row_zip_final_drain_seconds:.2f}s; "
                        f"archives_written={row_zip_archives_written}; "
                        f"new_members={row_zip_new_members}; "
                        f"MiB_written={row_zip_bytes_written/1024/1024:.2f}; "
                        f"max_pending_rows={max_pending_row_writes}"
                    )

                accounted_seconds = (
                    move_seconds
                    +
                    capture_seconds
                    +
                    visual_audit_seconds
                    +
                    capture_gate_seconds
                    +
                    save_submit_seconds
                    +
                    save_backpressure_seconds
                    +
                    save_final_drain_seconds
                    +
                    row_zip_submit_seconds
                    +
                    row_zip_backpressure_seconds
                    +
                    row_zip_final_drain_seconds
                    +
                    angle_lookup_seconds
                )

                print(
                    f"    timing accounted: {accounted_seconds:.2f}s / "
                    f"{elapsed:.2f}s; residual={max(0.0, elapsed-accounted_seconds):.2f}s"
                )

                print(
                    f"    new captures: {captured_new}; "
                    f"existing reused: {skipped_existing}"
                )

                output_sync = {
                    "captures_copied": 0,
                    "captures_reused": 0,
                    "angles_copied": 0,
                    "angles_reused": 0,
                    "seconds": 0.0,
                }

                if local_model_output is not None:
                    print(
                        "    syncing local output cache -> shared..."
                    )

                    output_sync = sync_local_model_output(
                        local_model_output["root"],
                        root,
                        args.save_angle_labels,
                        workers=args.sync_workers,
                    )

                    print(
                        f"    output sync: captures copied="
                        f"{output_sync['captures_copied']}, "
                        f"reused={output_sync['captures_reused']}; "
                        f"angles copied={output_sync['angles_copied']}; "
                        f"{output_sync['mib']:.1f} MiB; "
                        f"{output_sync['seconds']:.2f}s; "
                        f"{output_sync['mib_per_sec']:.2f} MiB/s; "
                        f"sync_workers={args.sync_workers}"
                    )

                (
                    shared_final_file_states,
                    final_file_scan_seconds,
                ) = scan_existing_capture_states(
                    shared_capture_dir,
                    model,
                )

                (
                    shared_final_zip_states,
                    shared_final_zip_member_map,
                    final_zip_scan_seconds,
                ) = scan_existing_row_zip_states(
                    shared_row_zip_dir,
                    model,
                )

                shared_final_states = (
                    shared_final_file_states
                    |
                    shared_final_zip_states
                )

                final_scan_seconds = (
                    final_file_scan_seconds
                    +
                    final_zip_scan_seconds
                )

                print(
                    f"    final shared index: {len(shared_final_states)}/"
                    f"{expected_total} state(s) in {final_scan_seconds:.3f}s "
                    f"(files={len(shared_final_file_states)}, "
                    f"rowzip={len(shared_final_zip_states)})"
                )

                if (
                    not state_limit_hit
                    and
                    len(shared_final_states) != expected_total
                ):
                    missing_set = [
                        (hi, vi)
                        for vi in range(vertical_states)
                        for hi in range(horizontal_states)
                        if (hi, vi) not in shared_final_states
                    ]

                    raise RuntimeError(
                        f"Quedan {len(missing_set)} capturas faltantes "
                        f"después de sync. Primeras: {missing_set[:10]}"
                    )

                if args.capture_storage == "row-zip":
                    manifest_rows = build_manifest_rows_mixed_storage(
                        model,
                        shared_capture_dir,
                        shared_row_zip_dir,
                        shared_angle_dir,
                        loaded_title,
                        horizontal_states,
                        vertical_states,
                        args.save_angle_labels,
                        args.audit_every,
                        shared_final_file_states,
                        shared_final_zip_member_map,
                    )
                else:
                    manifest_rows = build_manifest_rows_fast(
                        model,
                        shared_capture_dir,
                        shared_angle_dir,
                        loaded_title,
                        horizontal_states,
                        vertical_states,
                        args.save_angle_labels,
                        args.audit_every,
                    )

                save_manifest_v2(
                    root
                    /
                    "manifest.csv",
                    manifest_rows,
                )

                if state_limit_hit:
                    print(
                        f"    BENCHMARK/STATE-LIMIT: {processed} states; "
                        "_COMPLETE.txt intentionally not written"
                    )

                    batch_rows.append({
                        "cf2": str(
                            cf2_path
                        ),
                        "model": model,
                        "status": "PARTIAL_BENCHMARK",
                        "horizontal_states": horizontal_states,
                        "vertical_states": vertical_states,
                        "n_images": len(
                            manifest_rows
                        ),
                        "message": "state-limit",
                    })

                    continue

                atomic_write_text(
                    complete_marker,
                    "complete\n"
                    + f"horizontal_states={horizontal_states}\n"
                    + f"vertical_states={vertical_states}\n"
                    + f"captures={expected_total}\n"
                    + f"worker_id={worker_id}\n"
                    + "strategy=balloon_spectra_v6_10_native_ready_hybrid_fast_open_row_zip_raw_worker_t_view_verified\n"
                    + f"capture_storage={args.capture_storage}\n",
                    encoding="utf-8",
                )

                print(
                    f"    COMPLETE: {expected_total} angular states verified"
                )

                if (
                    local_model_output is not None
                    and
                    not args.keep_local_output_cache
                ):
                    clean_local_model_output(
                        local_model_output["root"]
                    )

                    print(
                        "    local output cache cleaned"
                    )

                pass_completed += 1

                batch_rows.append({
                    "cf2": str(
                        cf2_path
                    ),
                    "model": model,
                    "status": "OK",
                    "horizontal_states": horizontal_states,
                    "vertical_states": vertical_states,
                    "n_images": expected_total,
                    "message": "",
                })

            except Exception as exc:
                if row_writer_executor is not None:
                    try:
                        row_writer_executor.shutdown(
                            wait=True
                        )
                    except Exception:
                        pass

                    row_writer_executor = None

                if executor is not None:
                    try:
                        executor.shutdown(
                            wait=True
                        )
                    except Exception:
                        pass

                    executor = None

                print(
                    "    ERROR:",
                    exc,
                )

                print(
                    "    SKIP: model aborted; _COMPLETE.txt was not written"
                )

                pass_errors += 1

                batch_rows.append({
                    "cf2": str(
                        cf2_path
                    ),
                    "model": model,
                    "status": "ERROR",
                    "horizontal_states": "",
                    "vertical_states": "",
                    "n_images": "",
                    "message": str(
                        exc
                    ),
                })

            finally:
                if row_writer_executor is not None:
                    try:
                        row_writer_executor.shutdown(
                            wait=False
                        )
                    except Exception:
                        pass

                    row_writer_executor = None

                if graph_capture_ctx is not None:
                    try:
                        graph_capture_ctx.close()
                    except Exception:
                        pass

                    graph_capture_ctx = None

                if angle_capture_ctx is not None:
                    try:
                        angle_capture_ctx.close()
                    except Exception:
                        pass

                    angle_capture_ctx = None

                if claim is not None:
                    claim.release()
                    claim = None


        after_summary = summarize_distributed_state(
            files,
            args.output_dir,
            claims_dir,
            stale_seconds,
            expected_h=expected_h,
            expected_v=expected_v,
            progress_every=0,
        )

        if previous_complete is None:
            global_delta = (
                after_summary["complete"]
                -
                before_summary["complete"]
            )
        else:
            global_delta = (
                after_summary["complete"]
                -
                previous_complete
            )

        previous_complete = after_summary["complete"]

        print()
        print("-" * 100)
        print(
            f"PASS {pass_index} SUMMARY | "
            f"claimed_by_this_worker={pass_claimed}, "
            f"completed_by_this_worker={pass_completed}, "
            f"errors_by_this_worker={pass_errors}"
        )

        print_global_status(
            after_summary,
            prefix="END",
        )

        print(
            f"    global completed since previous checkpoint: "
            f"{global_delta:+d}"
        )

        write_worker_progress(
            args.output_dir,
            worker_id,
            pass_index,
            after_summary,
            pass_claimed,
            pass_completed,
            pass_errors,
            idle_passes,
        )

        if args.state_limit is not None:
            print(
                "    state-limit active: stopping after this pass "
                "(benchmark mode)."
            )
            break

        if not args.distributed:
            break

        if args.single_pass:
            print(
                "    --single-pass active: scheduler will not rescan pending models."
            )
            break

        if after_summary["complete"] >= after_summary["total"]:
            print()
            print("=" * 100)
            print(
                f"ALL COMPLETE: {after_summary['complete']}/"
                f"{after_summary['total']} CF2 models."
            )
            print(
                "This worker can exit safely."
            )
            print("=" * 100)
            break

        # Active work on another worker is not an idle failure.
        if after_summary["busy"] > 0:
            idle_passes = 0
            print(
                f"    {after_summary['busy']} model(s) currently busy "
                "on another worker."
            )

        elif global_delta > 0 or pass_completed > 0:
            idle_passes = 0

        else:
            idle_passes += 1
            print(
                f"    no global progress with no active claims: "
                f"idle pass {idle_passes}/{max(1, args.max_idle_passes)}"
            )

        write_worker_progress(
            args.output_dir,
            worker_id,
            pass_index,
            after_summary,
            pass_claimed,
            pass_completed,
            pass_errors,
            idle_passes,
        )

        if (
            after_summary["busy"] == 0
            and
            idle_passes >= max(
                1,
                int(args.max_idle_passes),
            )
        ):
            print()
            print(
                "STOPPING WITH UNRESOLVED MODELS: "
                "no progress after the configured idle passes."
            )
            print(
                f"    pending={after_summary['pending']} "
                f"(available={after_summary['available']}, "
                f"busy={after_summary['busy']})"
            )
            print(
                "    unresolved list: "
                f"unresolved_models.{worker_id}.txt"
            )

            write_unresolved_models(
                args.output_dir,
                worker_id,
                after_summary,
            )
            break

        wait_seconds = max(
            0.5,
            float(args.multipass_wait_sec),
        )

        print(
            f"    waiting {wait_seconds:.1f}s before rescanning "
            f"{after_summary['pending']} pending model(s)..."
        )

        time.sleep(
            wait_seconds
        )

    batch_manifest = (
        args.output_dir
        /
        (
            f"batch_balloon_spectra.{worker_id}.csv"
            if args.distributed
            else "batch_balloon_spectra.csv"
        )
    )

    fields = [
        "cf2",
        "model",
        "status",
        "horizontal_states",
        "vertical_states",
        "n_images",
        "message",
    ]

    batch_tmp = batch_manifest.with_name(
        batch_manifest.name + f".part.{os.getpid()}"
    )

    try:
        with batch_tmp.open(
            "w",
            encoding="utf-8-sig",
            newline="",
        ) as f:
            writer = csv.DictWriter(
                f,
                fieldnames=fields,
            )

            writer.writeheader()
            writer.writerows(
                batch_rows
            )

        os.replace(
            str(batch_tmp),
            str(batch_manifest),
        )

    finally:
        try:
            if batch_tmp.exists():
                batch_tmp.unlink()
        except Exception:
            pass

    print()
    print(
        "Batch manifest:",
        batch_manifest,
    )


if __name__ == "__main__":
    main()
