# -*- coding: utf-8 -*-
"""
CATT-Acoustic batch CF2 extractor v8 - Rot outer loop
=================================

Automatiza todos los .CF2 usando la apertura estable validada:

    File
      -> Open Directivity
          -> Any Format...

Después, en el cuadro:
    Folder -> speaker_cf2
    File   -> <archivo.CF2>
    OK

Luego extrae:
    frecuencia x Rot x Arc

y guarda UN CSV por modelo con todas las frecuencias.

Tecnología:
    - pywin32: menús, HWND, controles, drag & drop
    - pandas: CSV
    - Pillow: sólo si se activan screenshots/overlays
    - matplotlib/numpy: sólo si se activan plots

IMPORTANTE
----------
Antes de ejecutar todo el corpus, pruebe con:

    MAX_FILES = 3

Cuando funcione correctamente:

    MAX_FILES = None

La captura de imágenes está desactivada por defecto.
"""

import os
import sys
import re
import csv
import json
import time
import math
import traceback
from pathlib import Path

import pandas as pd

import win32api
import win32con
import win32gui

from pywinauto import Application, Desktop


# ======================================================================
# CONFIGURACIÓN GENERAL
# ======================================================================

CF2_DIR = Path(
    r"C:\Users\Docker\AppData\Roaming\CATTDATA\SD\speaker_cf2"
)

CATT_FOLDER_NAME = "speaker_cf2"

OUT_ROOT = Path(
    r"C:\Users\Docker\Desktop\Shared\catt_ground_truth_batch"
)

# Calibración obtenida previamente:
CALIBRATION_JSON = Path(
    r"C:\Users\Docker\Desktop\Shared"
    r"\catt_ground_truth\catt_grid_calibration.json"
)

OUT_ROOT.mkdir(
    parents=True,
    exist_ok=True
)

AUTO_RESTART_STATE = (
    OUT_ROOT
    /
    "_auto_restart_state.json"
)


# ----------------------------------------------------------------------
# PROCESAMIENTO
# ----------------------------------------------------------------------

PROCESS_ALL_FREQUENCIES = True

# Para primera prueba use, por ejemplo:
# MAX_FILES = 3
#
# Para recorrer todo:
MAX_FILES = None

# Si ya existe el CSV final del modelo, se omite.

SKIP_COMPLETED = True

# ----------------------------------------------------------------------
# AUTO-RECUPERACIÓN
#
# Si ocurren N errores de MODELOS consecutivos, reinicia este mismo
# proceso Python automáticamente. CATT permanece abierto.
#
# Al arrancar nuevamente, SKIP_COMPLETED=True hace que los modelos con
# _complete.ok se omitan y el primer modelo incompleto se intente otra vez.
# ----------------------------------------------------------------------

AUTO_RESTART_AFTER_CONSECUTIVE_ERRORS = 2

# Evita un bucle infinito si hay archivos realmente incompatibles.
MAX_AUTO_RESTARTS_WITHOUT_SUCCESS = 3

AUTO_RESTART_DELAY_SEC = 2.0

# Cierra módulos Directivity que ya estén abiertos antes de iniciar.
CLOSE_EXISTING_DIRECTIVITY_AT_START = True

# Cierra el módulo Directivity después de cada archivo.
CLOSE_DIRECTIVITY_AFTER_EACH_FILE = True


# ----------------------------------------------------------------------
# IMÁGENES
# ----------------------------------------------------------------------

# Captura original de CATT.
CAPTURE_SCREENSHOTS = False

# Captura + puntos extraídos superpuestos.
CREATE_OVERLAY_IMAGES = False

# CATT a la izquierda + polar reconstruido a la derecha.
CREATE_SIDE_BY_SIDE_PLOTS = False


# ----------------------------------------------------------------------
# ROT
# ----------------------------------------------------------------------

ROT_STEP_DEG = 10
ROT_ANGLES = list(range(0, 360, ROT_STEP_DEG))
ROT_TOLERANCE_DEG = 3.0


# ----------------------------------------------------------------------
# ARC
# ----------------------------------------------------------------------

ARC_STEP_DEG = 10
ARC_IDS = list(range(1, 19))


# ----------------------------------------------------------------------
# TIMINGS
# ----------------------------------------------------------------------

MENU_DIALOG_TIMEOUT = 8.0
FILE_LOAD_TIMEOUT = 12.0

AFTER_FOLDER_SELECT_DELAY = 0.40
AFTER_FILE_SELECT_DELAY = 0.20
AFTER_OK_DELAY = 0.40

DRAG_DURATION = 0.20
DRAG_STEPS = 25
AFTER_DRAG_DELAY = 0.20
AFTER_CURVE_UPDATE_DELAY = 0.08

AFTER_FREQUENCY_DELAY = 0.20
AFTER_SCREENSHOT_DELAY = 0.12
AFTER_CLOSE_DELAY = 0.30


# Calibración original realizada con polar 390x390.
REFERENCE_POLAR_DIAMETER_PX = 390.0

# ----------------------------------------------------------------------
# DIAGNÓSTICO DE ROT / UI
# ----------------------------------------------------------------------

DEBUG_ROTATION = True

# Maximizar CATT y la ventana MDI Directivity antes de mover Rot.
MAXIMIZE_CATT_MAIN = True
MAXIMIZE_DIRECTIVITY_CHILD = True

# Si el target del drag queda fuera del área visible de Directivity,
# abortar el intento con diagnóstico en vez de arrastrar sobre scrollbars.
CHECK_ROT_TARGET_VISIBLE = True
ROT_TARGET_VISIBLE_MARGIN_PX = 8

# Guarda screenshot SOLO cuando un Rot finalmente no puede alcanzarse.
CAPTURE_ON_ROTATION_FAILURE = True

# Guarda CSV con cada intento/bias del Rot que falla o requiere corrección.
SAVE_ROTATION_ATTEMPT_LOG = True

# En producción no guardar logs de intentos que finalmente tuvieron éxito.
# Los logs *_FAILED_attempts.csv se conservan siempre que
# SAVE_ROTATION_ATTEMPT_LOG=True.
SAVE_SUCCESSFUL_ROTATION_RETRY_LOGS = False

# ----------------------------------------------------------------------
# V8: ROT EXTERNO / FRECUENCIA INTERNA
#
# v7:
#   8 frecuencias x 36 Rot = hasta 288 drags del marcador.
#
# v8:
#   36 Rot x 8 frecuencias = normalmente 36 drags.
#
# La frecuencia cambia con un simple click y es mucho más barata que
# arrastrar + esperar estabilización del marcador Rot.
# ----------------------------------------------------------------------

ROT_OUTER_LOOP = True

# Alterna el orden de frecuencias:
#   Rot 0°  : 125 -> ... -> 16000
#   Rot 10° : 16000 -> ... -> 125
# Esto evita un cambio de frecuencia adicional entre Rot consecutivos.
SNAKE_FREQUENCY_ORDER = True

# Después de cada cambio de frecuencia, verificar que el marcador Rot
# sigue en el ángulo solicitado. Si CATT lo altera, corregirlo.
VERIFY_ROT_AFTER_FREQUENCY_CHANGE = True

# Checkpoint cada N Rot. Con 36 Rot y N=6:
#   6 checkpoints intermedios + guardado final.
CHECKPOINT_EVERY_N_ROTATIONS = 6

# Esperar a que el marcador deje de moverse después de cada drag.
ROT_STABLE_TIMEOUT_SEC = 1.2
ROT_STABLE_SAMPLE_DELAY_SEC = 0.08
ROT_STABLE_REQUIRED_SAMPLES = 2
ROT_STABLE_TOLERANCE_DEG = 0.20

# EnumChildWindows puede fallar mientras CATT recrea controles.
CONTROL_ENUM_RETRIES = 6
CONTROL_ENUM_RETRY_DELAY_SEC = 0.15


# ======================================================================
# CONSTANTES WIN32
# ======================================================================

WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E

BM_GETCHECK = 0x00F0
BM_CLICK = 0x00F5

LB_FINDSTRINGEXACT = 0x01A2
LB_SETCURSEL = 0x0186
LB_GETCURSEL = 0x0188
LB_GETTEXT = 0x0189
LB_GETTEXTLEN = 0x018A
LB_GETCOUNT = 0x018B

LBN_SELCHANGE = 1


# ======================================================================
# UTILIDADES
# ======================================================================

def safe_filename(text):
    text = re.sub(r'[<>:"/\\|?*]', "_", str(text))
    text = re.sub(r"_+", "_", text)
    return text.strip("._ ") or "model"


def source_to_model_name(filename):
    return safe_filename(
        os.path.splitext(
            os.path.basename(filename)
        )[0]
    )


def center_of(control):
    return (
        (control["left"] + control["right"]) / 2.0,
        (control["top"] + control["bottom"]) / 2.0,
    )


def point_distance(a, b):
    return math.hypot(
        a[0] - b[0],
        a[1] - b[1]
    )


def angular_error(a, b):
    return abs(
        ((a - b + 180.0) % 360.0) - 180.0
    )


# ======================================================================
# TEXTO / CONTROLES
# ======================================================================

def wm_get_text(hwnd):
    try:
        return win32gui.GetWindowText(hwnd)
    except Exception:
        return ""


def get_controls(parent_hwnd):
    """
    Enumera controles con reintentos.

    CATT recrea algunos child HWND durante cambios de Rot/frecuencia.
    En ese intervalo EnumChildWindows puede lanzar:

        SystemError:
        <built-in function EnumChildWindows>
        returned a result with an error set

    Eso es una condición transitoria, no necesariamente un CF2 malo.
    """

    last_error = None

    for attempt in range(
        1,
        CONTROL_ENUM_RETRIES + 1
    ):

        if (
            not parent_hwnd
            or
            not win32gui.IsWindow(
                parent_hwnd
            )
        ):
            last_error = RuntimeError(
                "parent HWND inválido/destruido: {}".format(
                    parent_hwnd
                )
            )

            time.sleep(
                CONTROL_ENUM_RETRY_DELAY_SEC
            )

            continue

        rows = []

        def callback(hwnd, _):
            try:
                # El child puede desaparecer durante el callback.
                if not win32gui.IsWindow(hwnd):
                    return

                cls = win32gui.GetClassName(hwnd)
                cid = win32gui.GetDlgCtrlID(hwnd)
                text = wm_get_text(hwnd)

                left, top, right, bottom = (
                    win32gui.GetWindowRect(hwnd)
                )

                rows.append({
                    "hwnd": hwnd,
                    "id": cid,
                    "class": cls,
                    "text": text,
                    "visible": bool(
                        win32gui.IsWindowVisible(hwnd)
                    ),
                    "enabled": bool(
                        win32gui.IsWindowEnabled(hwnd)
                    ),
                    "left": left,
                    "top": top,
                    "right": right,
                    "bottom": bottom,
                    "width": right - left,
                    "height": bottom - top,
                })

            except Exception:
                # Un child individual puede desaparecer; ignorarlo.
                pass

        try:
            win32gui.EnumChildWindows(
                parent_hwnd,
                callback,
                None
            )

            # Para Directivity esperamos bastantes controles.
            # Una lista vacía en medio de un redraw se considera transitoria.
            if rows:
                return rows

            last_error = RuntimeError(
                "EnumChildWindows devolvió 0 controles."
            )

        except (SystemError, Exception) as exc:
            last_error = exc

        if DEBUG_ROTATION:
            print(
                "      UI enum retry {}/{}: {!r}".format(
                    attempt,
                    CONTROL_ENUM_RETRIES,
                    last_error
                )
            )

        time.sleep(
            CONTROL_ENUM_RETRY_DELAY_SEC
        )

    raise RuntimeError(
        "No pude enumerar controles de HWND={} después de {} intentos. "
        "Último error={!r}".format(
            parent_hwnd,
            CONTROL_ENUM_RETRIES,
            last_error
        )
    )


def control_by_id(
    controls,
    control_id,
    class_name=None
):
    for control in controls:

        if control["id"] != control_id:
            continue

        if (
            class_name is not None
            and control["class"] != class_name
        ):
            continue

        return control

    return None


def find_button_by_text(
    parent_hwnd,
    wanted_text
):
    wanted = wanted_text.strip().lower()

    for c in get_controls(parent_hwnd):

        if c["class"].lower() != "button":
            continue

        text = (
            c["text"]
            .replace("&", "")
            .strip()
            .lower()
        )

        if text == wanted:
            return c

    return None


# ======================================================================
# VENTANAS CATT
# ======================================================================

def find_catt_main():
    matches = []

    def callback(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return

            title = win32gui.GetWindowText(hwnd)

            if (
                title
                and
                "CATT-Acoustic" in title
            ):
                matches.append(
                    (hwnd, title)
                )

        except Exception:
            pass

    win32gui.EnumWindows(
        callback,
        None
    )

    if not matches:
        raise RuntimeError(
            "No encontré CATT-Acoustic."
        )

    # Ventana principal normalmente es la de mayor tamaño.
    matches.sort(
        key=lambda x: (
            win32gui.GetWindowRect(x[0])[2]
            -
            win32gui.GetWindowRect(x[0])[0]
        )
        *
        (
            win32gui.GetWindowRect(x[0])[3]
            -
            win32gui.GetWindowRect(x[0])[1]
        ),
        reverse=True
    )

    return matches[0]


def find_directivity_windows(main_hwnd):
    found = []

    def callback(hwnd, _):
        try:
            title = win32gui.GetWindowText(hwnd)

            if (
                title
                and
                title.startswith(
                    "Directivity -"
                )
            ):
                found.append(
                    (hwnd, title)
                )

        except Exception:
            pass

    win32gui.EnumChildWindows(
        main_hwnd,
        callback,
        None
    )

    return found


def find_directivity_window(
    main_hwnd,
    filename=None
):
    windows = find_directivity_windows(
        main_hwnd
    )

    if filename is not None:
        target = filename.lower()

        for hwnd, title in windows:
            if target in title.lower():
                return hwnd, title

    if windows:
        return windows[-1]

    return None


def bring_to_front(hwnd):
    try:
        win32gui.ShowWindow(
            hwnd,
            win32con.SW_RESTORE
        )

        win32gui.BringWindowToTop(hwnd)

        win32gui.SetForegroundWindow(
            hwnd
        )

    except Exception:
        pass

    time.sleep(0.25)


def close_directivity_windows(
    main_hwnd
):
    windows = find_directivity_windows(
        main_hwnd
    )

    for hwnd, title in windows:
        try:
            print(
                "Closing:",
                title
            )

            win32gui.SendMessage(
                hwnd,
                win32con.WM_CLOSE,
                0,
                0
            )

        except Exception as exc:
            print(
                "WARNING closing module:",
                repr(exc)
            )

    # Esperar brevemente a que CATT quite las child windows.
    deadline = (
        time.time()
        +
        2.0
    )

    while time.time() < deadline:

        if not find_directivity_windows(
            main_hwnd
        ):
            break

        time.sleep(
            0.10
        )

    time.sleep(
        AFTER_CLOSE_DELAY
    )


# ======================================================================
# MENÚ WIN32
# ======================================================================

def canonical_menu_text(text):
    text = str(text or "")

    # Quitar shortcut mostrado después de TAB.
    text = text.split("\t")[0]

    text = (
        text
        .replace("&", "")
        .replace(".", "")
        .strip()
        .lower()
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text


def find_menu_position(
    menu_handle,
    wanted
):
    wanted_c = canonical_menu_text(
        wanted
    )

    count = win32gui.GetMenuItemCount(
        menu_handle
    )

    for pos in range(count):

        text = win32gui.GetMenuString(
            menu_handle,
            pos,
            win32con.MF_BYPOSITION
        )

        if canonical_menu_text(text) == wanted_c:
            return pos, text

    return None


def invoke_menu_path(
    main_hwnd,
    path
):
    """
    Ejemplo:
        ["File", "Open Directivity", "Any Format"]
    """

    menu = win32gui.GetMenu(
        main_hwnd
    )

    if not menu:
        raise RuntimeError(
            "CATT no expone un menú Win32."
        )

    current_menu = menu

    for depth, wanted in enumerate(path):

        result = find_menu_position(
            current_menu,
            wanted
        )

        if result is None:
            raise RuntimeError(
                "No encontré menú {!r} en {}".format(
                    wanted,
                    path
                )
            )

        pos, displayed = result

        is_last = (
            depth
            ==
            len(path) - 1
        )

        if not is_last:

            submenu = win32gui.GetSubMenu(
                current_menu,
                pos
            )

            if not submenu:
                raise RuntimeError(
                    "El menú {!r} no tiene submenu.".format(
                        displayed
                    )
                )

            current_menu = submenu
            continue

        command_id = win32gui.GetMenuItemID(
            current_menu,
            pos
        )

        if command_id == -1:
            raise RuntimeError(
                "No pude obtener command ID de {!r}".format(
                    displayed
                )
            )

        win32gui.PostMessage(
            main_hwnd,
            win32con.WM_COMMAND,
            command_id,
            0
        )

        return command_id


# ======================================================================
# BUSCAR DIÁLOGO "Select a Directivity-file..."
# ======================================================================

def enumerate_all_visible_windows():
    rows = []

    def add(hwnd):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return

            rows.append({
                "hwnd": hwnd,
                "title": win32gui.GetWindowText(hwnd),
                "class": win32gui.GetClassName(hwnd),
                "rect": win32gui.GetWindowRect(hwnd),
            })

        except Exception:
            pass

    def top_callback(hwnd, _):
        add(hwnd)

        try:
            win32gui.EnumChildWindows(
                hwnd,
                lambda child, __: add(child),
                None
            )
        except Exception:
            pass

    win32gui.EnumWindows(
        top_callback,
        None
    )

    return rows


def find_open_directivity_dialog():
    for row in enumerate_all_visible_windows():

        title = (
            row["title"]
            or ""
        ).lower()

        if (
            "select a directivity-file"
            in title
        ):
            return row["hwnd"]

    return None


def wait_for_open_dialog(
    timeout=MENU_DIALOG_TIMEOUT
):
    deadline = time.time() + timeout

    while time.time() < deadline:

        hwnd = find_open_directivity_dialog()

        if hwnd:
            return hwnd

        time.sleep(0.10)

    raise RuntimeError(
        "No apareció el diálogo "
        "'Select a Directivity-file'."
    )


# ======================================================================
# LISTBOX HELPERS
# ======================================================================

def listbox_items(hwnd):
    count = win32gui.SendMessage(
        hwnd,
        LB_GETCOUNT,
        0,
        0
    )

    result = []

    for i in range(max(0, int(count))):
        length = win32gui.SendMessage(
            hwnd,
            LB_GETTEXTLEN,
            i,
            0
        )

        # PyWin32 puede devolver directamente texto con GetWindowText
        # sólo para la selección, así que aquí usamos pywinauto como
        # fallback si el control no expone items de forma simple.
        result.append(
            (i, int(length))
        )

    return result


def notify_listbox_selection(
    list_hwnd
):
    parent = win32gui.GetParent(
        list_hwnd
    )

    control_id = win32gui.GetDlgCtrlID(
        list_hwnd
    )

    wparam = win32api.MAKELONG(
        control_id,
        LBN_SELCHANGE
    )

    win32gui.SendMessage(
        parent,
        win32con.WM_COMMAND,
        wparam,
        list_hwnd
    )


def select_listbox_item_exact(
    list_hwnd,
    text
):
    """
    Primero intenta Win32 LB_FINDSTRINGEXACT.
    Si falla, usa pywinauto Win32.
    """

    idx = win32gui.SendMessage(
        list_hwnd,
        LB_FINDSTRINGEXACT,
        -1,
        text
    )

    if idx != -1:

        win32gui.SendMessage(
            list_hwnd,
            LB_SETCURSEL,
            idx,
            0
        )

        notify_listbox_selection(
            list_hwnd
        )

        return int(idx)

    # Fallback pywinauto.
    try:
        from pywinauto import Desktop

        wrapper = (
            Desktop(
                backend="win32"
            )
            .window(
                handle=list_hwnd
            )
            .wrapper_object()
        )

        wrapper.select(
            text
        )

        return wrapper.selected_index()

    except Exception as exc:
        raise RuntimeError(
            "No encontré {!r} en ListBox HWND={}. "
            "Fallback pywinauto también falló: {!r}".format(
                text,
                list_hwnd,
                exc
            )
        )


def identify_open_dialog_listboxes(
    dialog_hwnd
):
    listboxes = [
        c
        for c in get_controls(
            dialog_hwnd
        )
        if c["class"].lower()
        in (
            "listbox",
            "combobox"
        )
        and c["visible"]
    ]

    # Esperamos dos listas grandes:
    # izquierda = Folder
    # derecha   = File
    listboxes.sort(
        key=lambda c: (
            c["left"],
            c["top"]
        )
    )

    # Preferir ListBox.
    lbs = [
        c
        for c in listboxes
        if c["class"].lower()
        ==
        "listbox"
    ]

    if len(lbs) >= 2:
        return lbs[0], lbs[1]

    if len(listboxes) >= 2:
        return listboxes[0], listboxes[1]

    # Debug útil si CATT cambia de controles.
    debug = [
        (
            c["id"],
            c["class"],
            c["text"],
            c["left"],
            c["top"],
            c["width"],
            c["height"],
        )
        for c in get_controls(
            dialog_hwnd
        )
    ]

    raise RuntimeError(
        "No pude identificar Folder/File ListBoxes. "
        "Controles={}".format(
            debug
        )
    )


# ======================================================================
# ERRORES MODALES
# ======================================================================

def find_error_popup():
    """
    Busca una ventana modal de CATT con botón OK.
    """

    candidates = []

    for row in enumerate_all_visible_windows():

        title = (
            row["title"]
            or ""
        )

        if (
            "CATT-Acoustic"
            not in title
        ):
            continue

        controls = get_controls(
            row["hwnd"]
        )

        ok_button = None
        static_texts = []

        for c in controls:

            if (
                c["class"].lower()
                ==
                "button"
                and
                c["text"]
                .replace("&", "")
                .strip()
                .lower()
                ==
                "ok"
            ):
                ok_button = c

            if (
                c["class"].lower()
                ==
                "static"
                and
                c["text"].strip()
            ):
                static_texts.append(
                    c["text"].strip()
                )

        if ok_button is not None:
            candidates.append(
                (
                    row["hwnd"],
                    ok_button,
                    " | ".join(
                        static_texts
                    )
                )
            )

    if candidates:
        return candidates[-1]

    return None


def dismiss_error_popup():
    result = find_error_popup()

    if result is None:
        return None

    hwnd, ok_button, message = result

    try:
        win32gui.SendMessage(
            ok_button["hwnd"],
            BM_CLICK,
            0,
            0
        )

        time.sleep(0.25)

    except Exception:
        pass

    return message or "CATT modal error"



# ======================================================================
# APERTURA ESTABLE CON PYWINAUTO
# ======================================================================

def connect_catt_main_window(main_hwnd):
    """
    Devuelve el wrapper pywinauto de la ventana principal que ya
    localizamos con find_catt_main().
    """
    app = Application(
        backend="win32"
    ).connect(
        handle=main_hwnd
    )

    window = app.window(
        handle=main_hwnd
    )

    try:
        window.restore()
    except Exception:
        pass

    try:
        window.set_focus()
    except Exception:
        pass

    return window


def try_menu_select_stable(
    main_window,
    candidates
):
    """
    Usa menu_select de pywinauto.

    Evita win32gui.GetMenuString, que no está disponible en
    la versión de pywin32 del Windows 7 usado aquí.
    """
    last_error = None

    for path in candidates:
        try:
            print(
                "Menu:",
                path
            )

            main_window.menu_select(
                path
            )

            return path

        except Exception as exc:
            last_error = exc

    raise RuntimeError(
        "No pude ejecutar el menú.\n"
        "Intentos:\n  {}\n"
        "Último error: {!r}".format(
            "\n  ".join(
                candidates
            ),
            last_error
        )
    )


def directivity_module_is_active(
    main_window
):
    """
    Window -> Directivity es un TOGGLE.

    Si está marcado, NO debemos volver a pulsarlo, porque eso
    desactiva el módulo y hace desaparecer:
        File -> Open Directivity
    """

    for path in (
        "Window->Directivity",
        "&Window->Directivity",
    ):
        try:
            item = main_window.menu_item(
                path
            )

            try:
                checked = bool(
                    item.is_checked()
                )

                print(
                    "Directivity checked:",
                    checked
                )

                return checked

            except Exception:
                pass

        except Exception:
            pass

    # Fallback observado en CATT:
    # si File -> Open Directivity existe, el módulo Directivity
    # ya está activo.
    for path in (
        "File->Open Directivity",
        "&File->Open Directivity",
    ):
        try:
            main_window.menu_item(
                path
            )

            print(
                "Directivity detectado por "
                "File -> Open Directivity."
            )

            return True

        except Exception:
            pass

    return False


def ensure_directivity_module_active(
    main_window
):
    """
    Activa Directivity sólo si está desactivado.
    """

    if directivity_module_is_active(
        main_window
    ):
        print(
            "OK: módulo Directivity ya está activo."
        )

        return

    print(
        "Directivity desactivado. "
        "Activando Window -> Directivity..."
    )

    try_menu_select_stable(
        main_window,
        [
            "Window->Directivity",
            "&Window->Directivity",
        ]
    )

    time.sleep(
        0.8
    )

    if directivity_module_is_active(
        main_window
    ):
        print(
            "OK: módulo Directivity activado."
        )
    else:
        print(
            "WARNING: no pude confirmar el check de "
            "Window -> Directivity después del click."
        )


def open_any_format_dialog_stable(
    main_window
):
    """
    File -> Open Directivity -> Any Format...
    """

    try_menu_select_stable(
        main_window,
        [
            "File->Open Directivity->Any Format...",
            "File->Open Directivity->Any Format",
            "&File->Open Directivity->Any Format...",
            "&File->Open Directivity->Any Format",
        ]
    )

    time.sleep(
        0.8
    )


def find_file_dialog_stable(
    timeout=MENU_DIALOG_TIMEOUT
):
    """
    Busca:
      Select a Directivity-file (SD0 SD1 SD2 CF1 CF2 CTA CBA)
    """

    desktop = Desktop(
        backend="win32"
    )

    deadline = (
        time.time()
        +
        timeout
    )

    while time.time() < deadline:

        for window in desktop.windows():

            try:
                title = (
                    window.window_text()
                    or ""
                )

                if (
                    "select a directivity-file"
                    in title.lower()
                ):
                    return window

            except Exception:
                pass

        time.sleep(
            0.15
        )

    raise RuntimeError(
        "No apareció el diálogo "
        "'Select a Directivity-file'."
    )


def dump_dialog_controls_stable(
    dialog
):
    print()
    print(
        "CONTROLES DEL DIALOGO"
    )
    print(
        "-" * 90
    )

    for control in dialog.descendants():

        try:
            rect = control.rectangle()

            print(
                "class={!r:18s} text={!r:38s} "
                "L={} T={} W={} H={}".format(
                    control.friendly_class_name(),
                    control.window_text(),
                    rect.left,
                    rect.top,
                    rect.width(),
                    rect.height(),
                )
            )

        except Exception:
            pass

    print(
        "-" * 90
    )


def get_folder_and_file_lists_stable(
    dialog
):
    """
    Las dos ListBox grandes del diálogo son:

      izquierda -> Folder
      derecha   -> File
    """

    listboxes = []

    for control in dialog.descendants():

        try:
            if (
                control.friendly_class_name()
                !=
                "ListBox"
            ):
                continue

            rect = control.rectangle()

            if (
                rect.width() > 100
                and
                rect.height() > 80
            ):
                listboxes.append(
                    control
                )

        except Exception:
            pass

    if len(listboxes) < 2:

        dump_dialog_controls_stable(
            dialog
        )

        raise RuntimeError(
            "Esperaba 2 ListBox grandes; "
            "encontré {}.".format(
                len(listboxes)
            )
        )

    listboxes.sort(
        key=lambda c:
            c.rectangle().left
    )

    return (
        listboxes[0],
        listboxes[-1]
    )


def select_folder_stable(
    dialog
):
    folder_list, _ = (
        get_folder_and_file_lists_stable(
            dialog
        )
    )

    print(
        "Folder:",
        CATT_FOLDER_NAME
    )

    try:
        folder_list.select(
            CATT_FOLDER_NAME
        )

    except Exception:

        folder_list.get_item(
            CATT_FOLDER_NAME
        ).click_input()

    time.sleep(
        AFTER_FOLDER_SELECT_DELAY
    )


def select_file_stable(
    dialog,
    filename
):
    # Releer las listas después de cambiar la carpeta.
    _, file_list = (
        get_folder_and_file_lists_stable(
            dialog
        )
    )

    print(
        "File:",
        filename
    )

    try:
        file_list.select(
            filename
        )

    except Exception:

        try:
            file_list.get_item(
                filename
            ).click_input()

        except Exception as exc:

            try:
                names = (
                    file_list.item_texts()
                )
            except Exception:
                names = []

            raise RuntimeError(
                "No pude seleccionar {!r}.\n"
                "Primeros archivos visibles: {}\n"
                "Error: {!r}".format(
                    filename,
                    names[:30],
                    exc
                )
            )

    time.sleep(
        AFTER_FILE_SELECT_DELAY
    )


def click_dialog_ok_stable(
    dialog
):
    """
    Pulsa OK en el selector de Directivity.

    El diálogo de CATT puede refrescar/recrear controles después
    de seleccionar Folder/File, por eso primero volvemos a obtener
    el diálogo actual.

    Fallback final:
        ENTER, porque OK es el botón por defecto.
    """

    print(
        "Click OK"
    )

    # Reobtener diálogo actual: evita wrappers obsoletos.
    try:
        dialog = find_file_dialog_stable(
            timeout=2.0
        )
    except Exception:
        pass

    # ----------------------------------------------------------
    # 1) Button por título exacto
    # ----------------------------------------------------------
    for title in (
        "OK",
        "&OK",
    ):
        try:
            button = (
                dialog.child_window(
                    title=title,
                    class_name="Button"
                )
                .wrapper_object()
            )

            if button.is_enabled():
                button.click()
                return

        except Exception:
            pass

    # ----------------------------------------------------------
    # 2) Buscar cualquier Button cuyo texto normalizado sea OK
    # ----------------------------------------------------------
    try:
        for control in dialog.descendants():

            try:
                if (
                    control.friendly_class_name()
                    ==
                    "Button"
                    and
                    control.window_text()
                    .replace("&", "")
                    .strip()
                    .lower()
                    ==
                    "ok"
                ):
                    if control.is_enabled():
                        control.click()
                        return

            except Exception:
                pass
    except Exception:
        pass

    # ----------------------------------------------------------
    # 3) ENTER sobre el diálogo.
    #    En CATT, OK es el default button.
    # ----------------------------------------------------------
    try:
        print(
            "OK no localizado por control; "
            "usando ENTER como fallback."
        )

        dialog.set_focus()
        dialog.type_keys(
            "{ENTER}"
        )

        time.sleep(
            0.30
        )

        # Si el selector desapareció, ENTER funcionó.
        try:
            fresh = find_file_dialog_stable(
                timeout=0.8
            )
        except Exception:
            return

        # Si sigue abierto, no asumimos éxito.
        dialog = fresh

    except Exception:
        pass

    raise RuntimeError(
        "No encontré ni pude activar el botón OK."
    )


def find_loaded_directivity_stable(
    filename,
    timeout=FILE_LOAD_TIMEOUT
):
    """
    Retorna wrapper pywinauto de:
        Directivity - ...filename...
    """

    target = filename.lower()

    desktop = Desktop(
        backend="win32"
    )

    deadline = (
        time.time()
        +
        timeout
    )

    while time.time() < deadline:

        # Si CATT abrió un modal de error, salir rápido.
        try:
            popup = find_error_popup()

            if popup is not None:
                _, _, message = popup

                dismiss_error_popup()

                raise RuntimeError(
                    "CATT rechazó {}: {}".format(
                        filename,
                        message
                    )
                )
        except RuntimeError:
            raise
        except Exception:
            pass

        for top in desktop.windows():

            try:
                title = (
                    top.window_text()
                    or ""
                )

                if (
                    "directivity -"
                    in title.lower()
                    and
                    target
                    in title.lower()
                ):
                    return top

                for child in top.descendants():

                    try:
                        child_title = (
                            child.window_text()
                            or ""
                        )

                        if (
                            "directivity -"
                            in child_title.lower()
                            and
                            target
                            in child_title.lower()
                        ):
                            return child

                    except Exception:
                        pass

            except Exception:
                pass

        time.sleep(
            0.20
        )

    return None




# ======================================================================
# RECUPERACIÓN / LIMPIEZA DE DIÁLOGOS MODALES
# ======================================================================

def get_wrapper_hwnd(wrapper):
    try:
        return int(wrapper.handle)
    except Exception:
        try:
            return int(wrapper.element_info.handle)
        except Exception:
            return None


def close_file_dialog_stable(dialog):
    """
    Cierra de forma segura el diálogo:
        Select a Directivity-file ...

    Se intenta:
        Cancel -> ESC -> WM_CLOSE
    """

    # 1) Cancel
    for title in (
        "Cancel",
        "&Cancel",
    ):
        try:
            button = (
                dialog.child_window(
                    title=title,
                    class_name="Button"
                )
                .wrapper_object()
            )

            if button.is_enabled():
                button.click()
                time.sleep(0.20)
                return True
        except Exception:
            pass

    # 2) ESC
    try:
        dialog.set_focus()
        dialog.type_keys(
            "{ESC}"
        )
        time.sleep(0.20)

        hwnd = get_wrapper_hwnd(
            dialog
        )

        if (
            hwnd is None
            or
            not win32gui.IsWindow(hwnd)
        ):
            return True
    except Exception:
        pass

    # 3) WM_CLOSE
    hwnd = get_wrapper_hwnd(
        dialog
    )

    if (
        hwnd is not None
        and
        win32gui.IsWindow(hwnd)
    ):
        try:
            win32gui.PostMessage(
                hwnd,
                win32con.WM_CLOSE,
                0,
                0
            )

            time.sleep(0.20)
            return True
        except Exception:
            pass

    return False


def cleanup_catt_transients():
    """
    Muy importante para el batch.

    Si un archivo falla mientras está abierto el selector de CF2
    o un popup modal, CATT deja deshabilitado el menú principal.
    Eso fue la causa del efecto cascada ElementNotEnabled().

    Esta función:
      1. cierra todos los diálogos "Select a Directivity-file"
      2. cierra popups CATT con OK
    """

    cleaned = False

    # ----------------------------------------------------------
    # A) Select a Directivity-file
    # ----------------------------------------------------------
    try:
        desktop = Desktop(
            backend="win32"
        )

        for window in desktop.windows():

            try:
                title = (
                    window.window_text()
                    or ""
                )

                if (
                    "select a directivity-file"
                    in title.lower()
                ):
                    print(
                        "Cleanup dialog:",
                        title
                    )

                    close_file_dialog_stable(
                        window
                    )

                    cleaned = True

            except Exception:
                pass

    except Exception:
        pass

    # ----------------------------------------------------------
    # B) Popups modales CATT con botón OK
    # ----------------------------------------------------------
    for _ in range(8):

        try:
            popup = find_error_popup()

            if popup is None:
                break

            hwnd, ok_button, message = popup

            print(
                "Cleanup popup:",
                message
            )

            try:
                win32gui.SendMessage(
                    ok_button["hwnd"],
                    BM_CLICK,
                    0,
                    0
                )
            except Exception:
                try:
                    win32gui.PostMessage(
                        hwnd,
                        win32con.WM_CLOSE,
                        0,
                        0
                    )
                except Exception:
                    pass

            cleaned = True

            time.sleep(
                0.20
            )

        except Exception:
            break

    if cleaned:
        time.sleep(
            0.30
        )

    return cleaned


def wait_main_window_enabled(
    main_window,
    timeout=3.0
):
    """
    Espera a que la ventana principal vuelva a aceptar comandos
    después de cerrar un modal.
    """

    deadline = (
        time.time()
        +
        timeout
    )

    while time.time() < deadline:

        try:
            if main_window.is_enabled():
                return True
        except Exception:
            pass

        cleanup_catt_transients()

        time.sleep(
            0.15
        )

    return False



# ======================================================================
# ABRIR CF2
# ======================================================================

def open_cf2_with_any_format(
    main_hwnd,
    main_window,
    filename
):
    """
    Apertura robusta para batch.

    Flujo:
        Window -> Directivity    (sólo si está desactivado)
        File -> Open Directivity -> Any Format...
        Folder -> speaker_cf2
        File -> filename
        OK

    Si quedó un diálogo modal del archivo anterior, lo limpia y
    reintenta. Esto evita la cascada ElementNotEnabled().
    """

    print(
        "Opening:",
        filename
    )

    last_error = None

    for open_attempt in range(
        1,
        4
    ):

        print(
            "  Open attempt {}/3".format(
                open_attempt
            )
        )

        try:
            # --------------------------------------------------
            # 0) Limpiar cualquier modal que haya quedado vivo.
            # --------------------------------------------------
            cleanup_catt_transients()

            if not wait_main_window_enabled(
                main_window,
                timeout=2.5
            ):
                raise RuntimeError(
                    "La ventana principal de CATT continúa "
                    "deshabilitada por un diálogo modal."
                )

            try:
                main_window.set_focus()
            except Exception:
                pass

            # --------------------------------------------------
            # 1) Window -> Directivity
            #    SOLO si está desactivado.
            # --------------------------------------------------
            ensure_directivity_module_active(
                main_window
            )

            time.sleep(
                0.25
            )

            # --------------------------------------------------
            # 2) File -> Open Directivity -> Any Format...
            # --------------------------------------------------
            open_any_format_dialog_stable(
                main_window
            )

            # --------------------------------------------------
            # 3) Selector
            # --------------------------------------------------
            dialog = find_file_dialog_stable()

            print(
                "Dialog:",
                dialog.window_text()
            )

            try:
                dialog.set_focus()
            except Exception:
                pass

            # --------------------------------------------------
            # 4) Folder
            # --------------------------------------------------
            select_folder_stable(
                dialog
            )

            # Reobtener wrapper después del refresh.
            dialog = find_file_dialog_stable(
                timeout=2.0
            )

            # --------------------------------------------------
            # 5) File
            # --------------------------------------------------
            select_file_stable(
                dialog,
                filename
            )

            # Reobtener otra vez porque CATT puede refrescarlo.
            dialog = find_file_dialog_stable(
                timeout=2.0
            )

            # --------------------------------------------------
            # 6) OK
            # --------------------------------------------------
            click_dialog_ok_stable(
                dialog
            )

            time.sleep(
                AFTER_OK_DELAY
            )

            # --------------------------------------------------
            # 7) Validar carga.
            # --------------------------------------------------
            loaded = find_loaded_directivity_stable(
                filename,
                timeout=FILE_LOAD_TIMEOUT
            )

            if loaded is None:
                raise RuntimeError(
                    "El diálogo fue procesado, pero no encontré "
                    "la ventana Directivity para: {}".format(
                        filename
                    )
                )

            hwnd = get_wrapper_hwnd(
                loaded
            )

            if hwnd is None:
                raise RuntimeError(
                    "Directivity cargó, pero no pude obtener su HWND."
                )

            try:
                title = loaded.window_text()
            except Exception:
                title = (
                    "Directivity - "
                    +
                    filename
                )

            print(
                "Loaded:",
                title
            )

            # CRÍTICO: antes de comenzar cualquier drag de Rot,
            # maximizar CATT + la MDI child Directivity.
            ensure_catt_directivity_maximized(
                main_hwnd,
                hwnd
            )

            print(
                "Directivity maximized before extraction."
            )

            return (
                hwnd,
                title
            )

        except Exception as exc:

            last_error = exc

            print(
                "  WARNING open attempt {} failed: {!r}".format(
                    open_attempt,
                    exc
                )
            )

            # Cerrar selector/popup que haya quedado bloqueando CATT.
            cleanup_catt_transients()

            time.sleep(
                0.40
            )

    raise RuntimeError(
        "No pude abrir {} después de 3 intentos. "
        "Último error: {!r}".format(
            filename,
            last_error
        )
    )


# ======================================================================
# CALIBRACIÓN
# ======================================================================

def load_calibration():

    if CALIBRATION_JSON.is_file():

        with CALIBRATION_JSON.open(
            "r",
            encoding="utf-8"
        ) as f:
            return json.load(f)

    # Fallback medido previamente.
    return {
        "r0_px": 143.0,
        "r_minus50_px": 12.0,
        "ring_step_px": 26.2,
        "center_db_extrapolated":
            -54.58015267175573,
    }


def scaled_calibration(
    calibration,
    polar
):
    diameter = min(
        polar["width"],
        polar["height"]
    )

    scale = (
        float(diameter)
        /
        REFERENCE_POLAR_DIAMETER_PX
    )

    result = dict(
        calibration
    )

    result["scale_factor"] = scale

    result["r0_scaled_px"] = (
        float(calibration["r0_px"])
        *
        scale
    )

    result["r_minus50_scaled_px"] = (
        float(calibration["r_minus50_px"])
        *
        scale
    )

    result["ring_step_scaled_px"] = (
        float(calibration["ring_step_px"])
        *
        scale
    )

    return result


def radius_to_db(
    radius_px,
    calibration
):
    r0 = calibration[
        "r0_scaled_px"
    ]

    step = calibration[
        "ring_step_scaled_px"
    ]

    db = (
        10.0
        *
        (
            radius_px
            -
            r0
        )
        /
        step
    )

    # CATT normalizado: no superar 0 dB.
    return min(
        0.0,
        float(db)
    )



# ======================================================================
# MAXIMIZACIÓN / VISIBILIDAD
# ======================================================================

def maximize_catt_main_window(main_hwnd):
    if not MAXIMIZE_CATT_MAIN:
        return

    try:
        if (
            main_hwnd
            and
            win32gui.IsWindow(main_hwnd)
        ):
            win32gui.ShowWindow(
                main_hwnd,
                win32con.SW_MAXIMIZE
            )
            time.sleep(0.50)
    except Exception as exc:
        print(
            "WARNING: no pude maximizar CATT main:",
            repr(exc)
        )


def maximize_directivity_window(directivity_hwnd):
    """
    Maximiza la MDI child 'Directivity - ...'.

    El bug observado aparece cuando esta child queda restaurada:
    los polares quedan recortados y aparecen scrollbars.
    El target de Rot puede entonces caer fuera del viewport visible,
    y el drag termina moviendo/auto-scrolleando la vista en vez del marcador.
    """
    if not MAXIMIZE_DIRECTIVITY_CHILD:
        return

    if (
        not directivity_hwnd
        or
        not win32gui.IsWindow(
            directivity_hwnd
        )
    ):
        raise RuntimeError(
            "Directivity HWND inválido al maximizar: {}".format(
                directivity_hwnd
            )
        )

    # Intento 1: ShowWindow sobre la MDI child.
    try:
        win32gui.ShowWindow(
            directivity_hwnd,
            win32con.SW_MAXIMIZE
        )
    except Exception:
        pass

    time.sleep(0.35)

    # Intento 2: WM_SYSCOMMAND / SC_MAXIMIZE.
    try:
        win32gui.SendMessage(
            directivity_hwnd,
            win32con.WM_SYSCOMMAND,
            win32con.SC_MAXIMIZE,
            0
        )
    except Exception:
        pass

    time.sleep(0.50)

    try:
        win32gui.BringWindowToTop(
            directivity_hwnd
        )
    except Exception:
        pass


def ensure_catt_directivity_maximized(
    main_hwnd,
    directivity_hwnd
):
    maximize_catt_main_window(
        main_hwnd
    )

    maximize_directivity_window(
        directivity_hwnd
    )

    # Esperar a que CATT termine el layout/redraw.
    time.sleep(0.60)


def directivity_visible_screen_rect(
    directivity_hwnd
):
    """
    Devuelve el rectángulo CLIENT visible de Directivity
    convertido a coordenadas de pantalla.
    """

    left, top, right, bottom = (
        win32gui.GetClientRect(
            directivity_hwnd
        )
    )

    p1 = win32gui.ClientToScreen(
        directivity_hwnd,
        (left, top)
    )

    p2 = win32gui.ClientToScreen(
        directivity_hwnd,
        (right, bottom)
    )

    return (
        p1[0],
        p1[1],
        p2[0],
        p2[1],
    )


def point_inside_rect_with_margin(
    point,
    rect,
    margin=0
):
    x, y = point
    left, top, right, bottom = rect

    return (
        left + margin
        <= x
        <= right - margin
        and
        top + margin
        <= y
        <= bottom - margin
    )


def assert_rot_target_visible(
    directivity_hwnd,
    target,
    requested_angle,
    aim_angle
):
    if not CHECK_ROT_TARGET_VISIBLE:
        return

    rect = directivity_visible_screen_rect(
        directivity_hwnd
    )

    if not point_inside_rect_with_margin(
        target,
        rect,
        ROT_TARGET_VISIBLE_MARGIN_PX
    ):
        raise RuntimeError(
            "ROT_TARGET_OUTSIDE_VISIBLE_CLIENT: "
            "request={} aim={} target={} client_rect={}. "
            "La ventana Directivity parece recortada/no maximizada."
            .format(
                requested_angle,
                aim_angle,
                target,
                rect
            )
        )



# ======================================================================
# FRECUENCIAS
# ======================================================================

FREQUENCY_BUTTONS = {
    37: 125,
    38: 250,
    39: 500,
    40: 1000,
    41: 2000,
    42: 4000,
    43: 8000,
    44: 16000,
}


def read_current_frequency(
    directivity_hwnd
):
    controls = get_controls(
        directivity_hwnd
    )

    for button_id, hz in (
        FREQUENCY_BUTTONS.items()
    ):

        button = control_by_id(
            controls,
            button_id,
            "Button"
        )

        if button is None:
            continue

        checked = (
            win32gui.SendMessage(
                button["hwnd"],
                BM_GETCHECK,
                0,
                0
            )
        )

        if checked == 1:
            return hz

    return None


def select_frequency(
    directivity_hwnd,
    frequency_hz
):
    """
    Selecciona frecuencia sólo cuando realmente cambia.

    En v8 usamos orden snake, por lo que el primer punto de frecuencia
    de un Rot puede coincidir con la frecuencia actual. En ese caso
    evitamos un BM_CLICK y un redraw innecesarios.

    Retorna:
        True  -> hubo cambio/click
        False -> ya estaba seleccionada
    """

    target_id = None

    for button_id, hz in (
        FREQUENCY_BUTTONS.items()
    ):
        if hz == frequency_hz:
            target_id = button_id
            break

    if target_id is None:
        raise RuntimeError(
            "Frecuencia no soportada: {}".format(
                frequency_hz
            )
        )

    controls = get_controls(
        directivity_hwnd
    )

    button = control_by_id(
        controls,
        target_id,
        "Button"
    )

    if button is None:
        raise RuntimeError(
            "No encontré botón de {} Hz.".format(
                frequency_hz
            )
        )

    # Si ya está marcada, no tocarla.
    checked = win32gui.SendMessage(
        button["hwnd"],
        BM_GETCHECK,
        0,
        0
    )

    if checked == 1:
        return False

    win32gui.SendMessage(
        button["hwnd"],
        BM_CLICK,
        0,
        0
    )

    time.sleep(
        AFTER_FREQUENCY_DELAY
    )

    actual = read_current_frequency(
        directivity_hwnd
    )

    if actual != frequency_hz:
        raise RuntimeError(
            "No pude seleccionar {} Hz. "
            "Actual={}".format(
                frequency_hz,
                actual
            )
        )

    return True


# ======================================================================
# ROT
# ======================================================================

def rotation_angle_ccw(
    polar,
    marker
):
    cx, cy = center_of(
        polar
    )

    x, y = center_of(
        marker
    )

    dx = x - cx
    dy = cy - y

    angle = math.degrees(
        math.atan2(
            -dx,
            dy
        )
    )

    return angle % 360.0


def rotation_target_ccw(
    polar,
    radius,
    angle_deg
):
    cx, cy = center_of(
        polar
    )

    theta = math.radians(
        angle_deg
    )

    x = (
        cx
        -
        radius
        *
        math.sin(theta)
    )

    y = (
        cy
        -
        radius
        *
        math.cos(theta)
    )

    return (
        int(round(x)),
        int(round(y))
    )


def get_rotation_state(
    directivity_hwnd
):
    controls = get_controls(
        directivity_hwnd
    )

    polar = control_by_id(
        controls,
        1,
        "CATT_Polar1"
    )

    marker = control_by_id(
        controls,
        101,
        "CATT_DBlob1"
    )

    if polar is None:
        raise RuntimeError(
            "No encontré CATT_Polar1 ID=1 (Rot)."
        )

    if marker is None:
        raise RuntimeError(
            "No encontré CATT_DBlob1 ID=101."
        )

    p_center = center_of(
        polar
    )

    m_center = center_of(
        marker
    )

    return {
        "polar": polar,
        "marker": marker,
        "polar_center": p_center,
        "marker_center": m_center,
        "radius": point_distance(
            p_center,
            m_center
        ),
        "angle": rotation_angle_ccw(
            polar,
            marker
        ),
    }


def wait_rotation_state_stable(
    directivity_hwnd
):
    """
    No asumimos que AFTER_DRAG_DELAY sea suficiente.

    Polling hasta que el ángulo leído permanezca prácticamente igual
    varias muestras consecutivas.
    """

    deadline = (
        time.time()
        +
        ROT_STABLE_TIMEOUT_SEC
    )

    last_angle = None
    stable_count = 0
    last_state = None

    while time.time() < deadline:

        try:
            state = get_rotation_state(
                directivity_hwnd
            )

            angle = state[
                "angle"
            ]

            last_state = state

            if last_angle is not None:

                delta = angular_error(
                    angle,
                    last_angle
                )

                if (
                    delta
                    <=
                    ROT_STABLE_TOLERANCE_DEG
                ):
                    stable_count += 1
                else:
                    stable_count = 0

            last_angle = angle

            if (
                stable_count
                >=
                ROT_STABLE_REQUIRED_SAMPLES
            ):
                return state

        except Exception as exc:

            if DEBUG_ROTATION:
                print(
                    "      esperando UI estable:",
                    repr(exc)
                )

        time.sleep(
            ROT_STABLE_SAMPLE_DELAY_SEC
        )

    # Si tuvimos por lo menos una lectura válida, devolver la última.
    if last_state is not None:
        return last_state

    raise RuntimeError(
        "No pude leer un estado Rot estable."
    )


def save_rotation_debug_screenshot(
    directivity_hwnd,
    output_path
):
    try:
        from PIL import ImageGrab

        if (
            not directivity_hwnd
            or
            not win32gui.IsWindow(
                directivity_hwnd
            )
        ):
            return False

        left, top, right, bottom = (
            win32gui.GetWindowRect(
                directivity_hwnd
            )
        )

        image = ImageGrab.grab(
            bbox=(
                left,
                top,
                right,
                bottom
            )
        )

        image.save(
            str(output_path)
        )

        return True

    except Exception as exc:

        print(
            "      WARNING screenshot debug:",
            repr(exc)
        )

        return False


def write_rotation_attempt_log(
    attempts,
    output_path
):
    if not attempts:
        return

    try:
        pd.DataFrame(
            attempts
        ).to_csv(
            str(output_path),
            index=False,
            encoding="utf-8-sig"
        )
    except Exception as exc:
        print(
            "      WARNING rotation debug CSV:",
            repr(exc)
        )


def drag_mouse(
    start,
    end,
    duration=DRAG_DURATION,
    steps=DRAG_STEPS
):
    sx, sy = start
    ex, ey = end

    win32api.SetCursorPos(
        (
            int(round(sx)),
            int(round(sy))
        )
    )

    time.sleep(0.05)

    win32api.mouse_event(
        win32con.MOUSEEVENTF_LEFTDOWN,
        0,
        0,
        0,
        0
    )

    time.sleep(0.04)

    for i in range(
        1,
        steps + 1
    ):

        t = i / float(steps)

        x = sx + (ex - sx) * t
        y = sy + (ey - sy) * t

        win32api.SetCursorPos(
            (
                int(round(x)),
                int(round(y))
            )
        )

        time.sleep(
            duration
            /
            steps
        )

    win32api.mouse_event(
        win32con.MOUSEEVENTF_LEFTUP,
        0,
        0,
        0,
        0
    )

    time.sleep(
        AFTER_DRAG_DELAY
    )


def set_rotation_ccw(
    directivity_hwnd,
    requested_angle,
    debug_dir=None,
    debug_prefix=None
):
    """
    Posiciona Rot y registra los intentos cuando DEBUG_ROTATION=True.

    Hipótesis que estamos probando:
      - CATT cuantiza/snappea el marcador en bins de ~10°;
      - el redraw puede tardar más que el delay fijo;
      - en algunos sectores hace falta cruzar más de +8° para
        entrar en el bin siguiente.

    Por eso:
      1. esperamos estado estable después del drag;
      2. probamos biases mayores;
      3. si falla, guardamos screenshot + CSV de intentos.
    """

    requested_angle = (
        float(requested_angle)
        %
        360.0
    )

    # Incluye ±10/±12/±14 para el patrón observado:
    # request 270 -> actual ~259.8, etc.
    aim_biases = [
        0.0,
        4.0,
        6.0,
        8.0,
        10.0,
        12.0,
        14.0,
        -4.0,
        -6.0,
        -8.0,
        -10.0,
        -12.0,
        -14.0,
        2.0,
        -2.0,
    ]

    last_angle = None
    best_angle = None
    best_error = float("inf")

    attempts_log = []

    for attempt, bias in enumerate(
        aim_biases,
        start=1
    ):

        state = wait_rotation_state_stable(
            directivity_hwnd
        )

        current = state[
            "angle"
        ]

        current_error = angular_error(
            current,
            requested_angle
        )

        if (
            current_error
            <=
            ROT_TOLERANCE_DEG
        ):
            return current

        aim_angle = (
            requested_angle
            +
            bias
        ) % 360.0

        target = rotation_target_ccw(
            state["polar"],
            state["radius"],
            aim_angle
        )

        # No arrastrar si el punto objetivo queda fuera del viewport.
        # Eso fue exactamente lo observado cuando Directivity estaba
        # restaurada y con scrollbars.
        assert_rot_target_visible(
            directivity_hwnd,
            target,
            requested_angle,
            aim_angle
        )

        before_marker = state[
            "marker_center"
        ]

        drag_mouse(
            before_marker,
            target
        )

        # En vez de leer inmediatamente, esperar redraw/snap estable.
        state_after = wait_rotation_state_stable(
            directivity_hwnd
        )

        last_angle = state_after[
            "angle"
        ]

        error = angular_error(
            last_angle,
            requested_angle
        )

        if error < best_error:
            best_error = error
            best_angle = last_angle

        attempts_log.append({
            "attempt": attempt,
            "requested_angle_deg":
                requested_angle,
            "bias_deg":
                bias,
            "aim_angle_deg":
                aim_angle,
            "angle_before_deg":
                current,
            "angle_after_deg":
                last_angle,
            "error_after_deg":
                error,
            "marker_before_x":
                before_marker[0],
            "marker_before_y":
                before_marker[1],
            "target_x":
                target[0],
            "target_y":
                target[1],
            "marker_after_x":
                state_after[
                    "marker_center"
                ][0],
            "marker_after_y":
                state_after[
                    "marker_center"
                ][1],
            "polar_center_x":
                state_after[
                    "polar_center"
                ][0],
            "polar_center_y":
                state_after[
                    "polar_center"
                ][1],
            "marker_radius_px":
                state_after[
                    "radius"
                ],
            "directivity_hwnd":
                directivity_hwnd,
            "hwnd_valid":
                bool(
                    win32gui.IsWindow(
                        directivity_hwnd
                    )
                ),
        })

        print(
            "      Rot request={:6.1f} "
            "aim={:6.1f} "
            "bias={:+5.1f} "
            "actual={:7.2f} "
            "error={:5.2f}".format(
                requested_angle,
                aim_angle,
                bias,
                last_angle,
                error
            )
        )

        if (
            error
            <=
            ROT_TOLERANCE_DEG
        ):
            # Si necesitó más de un intento, conservar el log:
            # nos ayuda a mapear dónde están las fronteras de snap.
            if (
                SAVE_ROTATION_ATTEMPT_LOG
                and
                SAVE_SUCCESSFUL_ROTATION_RETRY_LOGS
                and
                debug_dir is not None
                and
                len(attempts_log) > 1
            ):
                Path(
                    debug_dir
                ).mkdir(
                    parents=True,
                    exist_ok=True
                )

                prefix = (
                    debug_prefix
                    or
                    "rot_{:03d}".format(
                        int(
                            requested_angle
                        )
                    )
                )

                write_rotation_attempt_log(
                    attempts_log,
                    Path(debug_dir)
                    /
                    (
                        prefix
                        +
                        "_attempts.csv"
                    )
                )

            return last_angle

    # ----------------------------------------------------------
    # FALLO FINAL: capturar evidencia visual + log.
    # ----------------------------------------------------------

    if debug_dir is not None:

        debug_dir = Path(
            debug_dir
        )

        debug_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        prefix = (
            debug_prefix
            or
            "rot_{:03d}".format(
                int(
                    requested_angle
                )
            )
        )

        if SAVE_ROTATION_ATTEMPT_LOG:

            write_rotation_attempt_log(
                attempts_log,
                debug_dir
                /
                (
                    prefix
                    +
                    "_FAILED_attempts.csv"
                )
            )

        if CAPTURE_ON_ROTATION_FAILURE:

            save_rotation_debug_screenshot(
                directivity_hwnd,
                debug_dir
                /
                (
                    prefix
                    +
                    "_FAILED.png"
                )
            )

    raise RuntimeError(
        "No pude posicionar Rot={}°. "
        "Mejor actual={}° error={:.2f}°; "
        "último actual={}°".format(
            requested_angle,
            best_angle,
            best_error,
            last_angle
        )
    )


# ======================================================================
# ARC
# ======================================================================

def arc_angle_geometry(
    polar,
    blob
):
    cx, cy = center_of(
        polar
    )

    x, y = center_of(
        blob
    )

    dx = x - cx
    dy = y - cy

    radius = math.hypot(
        dx,
        dy
    )

    if radius == 0:
        return None

    cosine = (
        (cy - y)
        /
        radius
    )

    cosine = max(
        -1.0,
        min(
            1.0,
            cosine
        )
    )

    return math.degrees(
        math.acos(
            cosine
        )
    )


def extract_current_arc(
    directivity_hwnd,
    frequency_hz,
    rot_requested,
    rot_actual,
    base_calibration,
    source_file
):
    controls = get_controls(
        directivity_hwnd
    )

    polar = control_by_id(
        controls,
        0,
        "CATT_Polar1"
    )

    if polar is None:
        raise RuntimeError(
            "No encontré CATT_Polar1 ID=0 (Arc)."
        )

    calibration = scaled_calibration(
        base_calibration,
        polar
    )

    blobs = sorted(
        [
            c
            for c in controls
            if (
                c["class"]
                ==
                "CATT_DBlob1"
                and
                1 <= c["id"] <= 18
            )
        ],
        key=lambda c:
            c["id"]
    )

    if len(blobs) != 18:
        raise RuntimeError(
            "Esperaba 18 Arc blobs; "
            "encontré {}".format(
                len(blobs)
            )
        )

    rows = []

    polar_center = center_of(
        polar
    )

    r0 = calibration[
        "r0_scaled_px"
    ]

    # Arc 0°.
    rows.append({
        "source_file": source_file,
        "frequency_hz": frequency_hz,
        "rot_requested_deg":
            float(rot_requested),
        "rot_actual_deg":
            float(rot_actual),
        "arc_deg": 0.0,
        "arc_geometry_deg": 0.0,
        "blob_id": 0,
        "radius_px": r0,
        "db_from_grid": 0.0,
        "screen_x": polar_center[0],
        "screen_y":
            polar_center[1] - r0,
        "r0_px":
            calibration[
                "r0_scaled_px"
            ],
        "r_minus50_px":
            calibration[
                "r_minus50_scaled_px"
            ],
        "ring_step_px":
            calibration[
                "ring_step_scaled_px"
            ],
        "polar_width_px":
            polar["width"],
        "polar_height_px":
            polar["height"],
        "scale_factor":
            calibration[
                "scale_factor"
            ],
        "source":
            "front_normalized",
    })

    # Arc 10°...180°.
    for blob in blobs:

        blob_center = center_of(
            blob
        )

        radius = point_distance(
            polar_center,
            blob_center
        )

        db = radius_to_db(
            radius,
            calibration
        )

        rows.append({
            "source_file":
                source_file,
            "frequency_hz":
                frequency_hz,
            "rot_requested_deg":
                float(rot_requested),
            "rot_actual_deg":
                float(rot_actual),
            "arc_deg":
                float(
                    blob["id"]
                    *
                    ARC_STEP_DEG
                ),
            "arc_geometry_deg":
                arc_angle_geometry(
                    polar,
                    blob
                ),
            "blob_id":
                blob["id"],
            "radius_px":
                radius,
            "db_from_grid":
                db,
            "screen_x":
                blob_center[0],
            "screen_y":
                blob_center[1],
            "r0_px":
                calibration[
                    "r0_scaled_px"
                ],
            "r_minus50_px":
                calibration[
                    "r_minus50_scaled_px"
                ],
            "ring_step_px":
                calibration[
                    "ring_step_scaled_px"
                ],
            "polar_width_px":
                polar["width"],
            "polar_height_px":
                polar["height"],
            "scale_factor":
                calibration[
                    "scale_factor"
                ],
            "source":
                "CATT_DBlob1_geometry",
        })

    return rows


# ======================================================================
# SCREENSHOT / VISUALIZACIÓN
# ======================================================================

def capture_directivity_window(
    directivity_hwnd
):
    try:
        from PIL import ImageGrab
    except ImportError:
        raise RuntimeError(
            "Pillow no instalado."
        )

    left, top, right, bottom = (
        win32gui.GetWindowRect(
            directivity_hwnd
        )
    )

    time.sleep(
        AFTER_SCREENSHOT_DELAY
    )

    image = ImageGrab.grab(
        bbox=(
            left,
            top,
            right,
            bottom
        )
    )

    return image, (
        left,
        top,
        right,
        bottom
    )


def image_filename(
    frequency_hz,
    rot_deg,
    suffix=""
):
    return (
        "{:05d}Hz_rot_{:03d}deg{}.png"
        .format(
            int(frequency_hz),
            int(rot_deg),
            suffix
        )
    )


def create_overlay_image(
    screenshot,
    window_rect,
    rows,
    output_path
):
    from PIL import ImageDraw

    image = screenshot.copy()
    draw = ImageDraw.Draw(
        image
    )

    window_left = window_rect[0]
    window_top = window_rect[1]

    points = []

    for row in rows:

        x = (
            row["screen_x"]
            -
            window_left
        )

        y = (
            row["screen_y"]
            -
            window_top
        )

        points.append(
            (
                int(round(x)),
                int(round(y))
            )
        )

    if len(points) >= 2:
        draw.line(
            points,
            fill=(255, 0, 0),
            width=2
        )

    for row, (x, y) in zip(
        rows,
        points
    ):
        radius = 3

        draw.ellipse(
            (
                x - radius,
                y - radius,
                x + radius,
                y + radius
            ),
            outline=(255, 0, 0),
            width=2
        )

        if int(row["arc_deg"]) % 30 == 0:

            draw.text(
                (
                    x + 5,
                    y - 11
                ),
                "{} deg {:.1f} dB".format(
                    int(row["arc_deg"]),
                    row["db_from_grid"]
                ),
                fill=(255, 0, 0)
            )

    first = rows[0]

    draw.text(
        (8, 8),
        "{} Hz | Rot {} deg CCW".format(
            int(first["frequency_hz"]),
            int(
                first[
                    "rot_requested_deg"
                ]
            )
        ),
        fill=(255, 0, 0)
    )

    image.save(
        str(output_path)
    )


def create_side_by_side_plot(
    screenshot,
    rows,
    output_path
):
    import numpy as np
    import matplotlib.pyplot as plt

    arc = np.array(
        [
            row["arc_deg"]
            for row in rows
        ],
        dtype=float
    )

    radius_px = np.array(
        [
            row["radius_px"]
            for row in rows
        ],
        dtype=float
    )

    db = np.array(
        [
            row["db_from_grid"]
            for row in rows
        ],
        dtype=float
    )

    first = rows[0]

    r0 = float(
        first["r0_px"]
    )

    ring_step = float(
        first["ring_step_px"]
    )

    center_db = (
        10.0
        *
        (0.0 - r0)
        /
        ring_step
    )

    db_ticks = np.array(
        [
            -50,
            -40,
            -30,
            -20,
            -10,
            0
        ],
        dtype=float
    )

    radius_ticks = (
        r0
        +
        (db_ticks / 10.0)
        *
        ring_step
    )

    theta = np.radians(
        arc
    )

    fig = plt.figure(
        figsize=(15, 8)
    )

    ax1 = fig.add_subplot(
        1,
        2,
        1
    )

    ax1.imshow(
        screenshot
    )

    ax1.axis(
        "off"
    )

    ax1.set_title(
        "CATT-Acoustic"
    )

    ax2 = fig.add_subplot(
        1,
        2,
        2,
        projection="polar"
    )

    ax2.plot(
        theta,
        radius_px,
        marker="o"
    )

    ax2.set_theta_zero_location(
        "N"
    )

    ax2.set_theta_direction(
        1
    )

    ax2.set_thetamin(
        0
    )

    ax2.set_thetamax(
        180
    )

    angular_ticks = np.array(
        [
            0,
            30,
            60,
            90,
            120,
            150,
            180
        ],
        dtype=float
    )

    ax2.set_xticks(
        np.radians(
            angular_ticks
        )
    )

    ax2.set_xticklabels(
        [
            "0°",
            "30°",
            "60°",
            "90°",
            "120°",
            "150°",
            "180°",
        ]
    )

    valid_radius_ticks = []
    valid_db_labels = []

    for r_tick, db_tick in zip(
        radius_ticks,
        db_ticks
    ):
        if r_tick >= 0:
            valid_radius_ticks.append(
                r_tick
            )

            valid_db_labels.append(
                "{} dB".format(
                    int(db_tick)
                )
            )

    ax2.set_yticks(
        valid_radius_ticks
    )

    ax2.set_yticklabels(
        valid_db_labels
    )

    maximum_radius = max(
        r0 * 1.04,
        float(
            np.nanmax(
                radius_px
            )
        )
        *
        1.04
    )

    ax2.set_ylim(
        0,
        maximum_radius
    )

    ax2.text(
        0,
        0,
        "{:.1f} dB".format(
            center_db
        ),
        horizontalalignment="center",
        verticalalignment="center"
    )

    ax2.set_title(
        "{} Hz | Rot {}° CCW".format(
            int(
                first[
                    "frequency_hz"
                ]
            ),
            int(
                first[
                    "rot_requested_deg"
                ]
            )
        ),
        pad=24
    )

    for angle, radius, value_db in zip(
        arc,
        radius_px,
        db
    ):

        if int(round(angle)) % 30 != 0:
            continue

        ax2.annotate(
            "{:.1f}".format(
                value_db
            ),
            xy=(
                math.radians(
                    angle
                ),
                radius
            ),
            xytext=(4, 4),
            textcoords="offset points",
            fontsize=8
        )

    fig.tight_layout()

    fig.savefig(
        str(output_path),
        dpi=150,
        bbox_inches="tight"
    )

    plt.close(fig)


# ======================================================================
# EXTRACCIÓN DE UN MODELO
# ======================================================================

def create_model_dirs(
    filename
):
    model = source_to_model_name(
        filename
    )

    model_dir = (
        OUT_ROOT
        /
        model
    )

    model_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    screenshots_dir = (
        model_dir
        /
        "screenshots"
    )

    overlays_dir = (
        model_dir
        /
        "overlays"
    )

    plots_dir = (
        model_dir
        /
        "plots"
    )

    debug_rotation_dir = (
        model_dir
        /
        "debug_rotation"
    )

    if CAPTURE_SCREENSHOTS:
        screenshots_dir.mkdir(
            exist_ok=True
        )

    if CREATE_OVERLAY_IMAGES:
        overlays_dir.mkdir(
            exist_ok=True
        )

    if CREATE_SIDE_BY_SIDE_PLOTS:
        plots_dir.mkdir(
            exist_ok=True
        )

    return {
        "model": model,
        "model_dir": model_dir,
        "csv":
            model_dir
            /
            (
                model
                +
                "_directivity_all.csv"
            ),
        "screenshots":
            screenshots_dir,
        "overlays":
            overlays_dir,
        "plots":
            plots_dir,
        "debug_rotation":
            debug_rotation_dir,
        "done":
            model_dir
            /
            "_complete.ok",
    }


def save_rows(
    rows,
    output_csv
):
    df = pd.DataFrame(
        rows
    )

    df = df.sort_values(
        [
            "frequency_hz",
            "rot_requested_deg",
            "arc_deg",
        ]
    )

    df.to_csv(
        str(output_csv),
        index=False,
        encoding="utf-8-sig"
    )

    return df


def extract_one_model(
    directivity_hwnd,
    source_file,
    calibration,
    paths
):
    """
    V8: Rot es el loop EXTERNO.

    Antes (v7):
        frequency -> Rot
        hasta 288 movimientos de marcador por modelo.

    Ahora:
        Rot -> frequency
        normalmente 36 movimientos de marcador por modelo.

    Se mantienen exactamente:
        8 frecuencias x 36 Rot x 19 Arc = 5472 filas.
    """

    all_rows = []

    if PROCESS_ALL_FREQUENCIES:
        frequencies = list(
            FREQUENCY_BUTTONS.values()
        )
    else:
        current = read_current_frequency(
            directivity_hwnd
        )

        if current is None:
            raise RuntimeError(
                "No pude detectar frecuencia."
            )

        frequencies = [
            current
        ]

    print()
    print(
        "    V8 LOOP: Rot externo -> frecuencia interna"
    )

    print(
        "    Rot positions:",
        len(ROT_ANGLES)
    )

    print(
        "    Frequencies:",
        len(frequencies)
    )

    # ----------------------------------------------------------
    # LOOP EXTERNO: ROT
    # ----------------------------------------------------------

    for r_index, rot_deg in enumerate(
        ROT_ANGLES,
        start=1
    ):

        print()
        print(
            "    ROT [{:02d}/{:02d}] {:3d}°".format(
                r_index,
                len(
                    ROT_ANGLES
                ),
                rot_deg
            )
        )

        # Sólo UN drag normal por Rot.
        rot_actual_anchor = set_rotation_ccw(
            directivity_hwnd,
            rot_deg,
            debug_dir=paths[
                "debug_rotation"
            ],
            debug_prefix=(
                "rot_{:03d}_position"
                .format(
                    int(
                        rot_deg
                    )
                )
            )
        )

        time.sleep(
            AFTER_CURVE_UPDATE_DELAY
        )

        # ------------------------------------------------------
        # SNAKE FREQUENCY ORDER
        #
        # Rot 0°  : 125 -> ... -> 16k
        # Rot 10° : 16k -> ... -> 125
        #
        # Así el primer punto del siguiente Rot normalmente ya
        # está en la frecuencia correcta.
        # ------------------------------------------------------

        if (
            SNAKE_FREQUENCY_ORDER
            and
            r_index % 2 == 0
        ):
            frequencies_this_rot = list(
                reversed(
                    frequencies
                )
            )
        else:
            frequencies_this_rot = list(
                frequencies
            )

        # ------------------------------------------------------
        # LOOP INTERNO: FRECUENCIA
        # ------------------------------------------------------

        for f_index, frequency_hz in enumerate(
            frequencies_this_rot,
            start=1
        ):

            changed_frequency = select_frequency(
                directivity_hwnd,
                frequency_hz
            )

            # Pequeño margen para que la curva Arc se actualice.
            if changed_frequency:
                time.sleep(
                    AFTER_CURVE_UPDATE_DELAY
                )

            # --------------------------------------------------
            # Verificación de seguridad:
            # cambiar frecuencia NO debería mover Rot.
            #
            # Si CATT sí lo altera, corregimos ese caso concreto.
            # --------------------------------------------------

            rot_actual = rot_actual_anchor

            if VERIFY_ROT_AFTER_FREQUENCY_CHANGE:

                try:
                    state_now = get_rotation_state(
                        directivity_hwnd
                    )

                    measured_rot = state_now[
                        "angle"
                    ]

                    rot_error = angular_error(
                        measured_rot,
                        rot_deg
                    )

                    if (
                        rot_error
                        <=
                        ROT_TOLERANCE_DEG
                    ):
                        rot_actual = measured_rot

                    else:
                        print(
                            "        WARNING Rot cambió tras "
                            "{} Hz: request={:.1f} actual={:.2f} "
                            "error={:.2f}; corrigiendo..."
                            .format(
                                frequency_hz,
                                float(
                                    rot_deg
                                ),
                                measured_rot,
                                rot_error
                            )
                        )

                        rot_actual = set_rotation_ccw(
                            directivity_hwnd,
                            rot_deg,
                            debug_dir=paths[
                                "debug_rotation"
                            ],
                            debug_prefix=(
                                "{:05d}Hz_rot_{:03d}_freq_repair"
                                .format(
                                    int(
                                        frequency_hz
                                    ),
                                    int(
                                        rot_deg
                                    )
                                )
                            )
                        )

                except Exception as exc:
                    print(
                        "        WARNING verificando Rot después "
                        "de frecuencia {} Hz: {!r}. "
                        "Intentando corrección."
                        .format(
                            frequency_hz,
                            exc
                        )
                    )

                    rot_actual = set_rotation_ccw(
                        directivity_hwnd,
                        rot_deg,
                        debug_dir=paths[
                            "debug_rotation"
                        ],
                        debug_prefix=(
                            "{:05d}Hz_rot_{:03d}_verify_repair"
                            .format(
                                int(
                                    frequency_hz
                                ),
                                int(
                                    rot_deg
                                )
                            )
                        )
                    )

            rows = extract_current_arc(
                directivity_hwnd,
                frequency_hz,
                rot_deg,
                rot_actual,
                calibration,
                source_file
            )

            all_rows.extend(
                rows
            )

            print(
                "        {:5d} Hz -> {:2d} Arc | "
                "Rot actual={:7.2f}° | total={}"
                .format(
                    int(
                        frequency_hz
                    ),
                    len(
                        rows
                    ),
                    float(
                        rot_actual
                    ),
                    len(
                        all_rows
                    )
                )
            )

            # --------------------------------------------------
            # Imágenes opcionales
            # --------------------------------------------------

            need_capture = (
                CAPTURE_SCREENSHOTS
                or
                CREATE_OVERLAY_IMAGES
                or
                CREATE_SIDE_BY_SIDE_PLOTS
            )

            if need_capture:

                bring_to_front(
                    directivity_hwnd
                )

                screenshot, window_rect = (
                    capture_directivity_window(
                        directivity_hwnd
                    )
                )

                name = image_filename(
                    frequency_hz,
                    rot_deg
                )

                if CAPTURE_SCREENSHOTS:

                    screenshot.save(
                        str(
                            paths[
                                "screenshots"
                            ]
                            /
                            name
                        )
                    )

                if CREATE_OVERLAY_IMAGES:

                    create_overlay_image(
                        screenshot,
                        window_rect,
                        rows,
                        paths[
                            "overlays"
                        ]
                        /
                        image_filename(
                            frequency_hz,
                            rot_deg,
                            "_overlay"
                        )
                    )

                if CREATE_SIDE_BY_SIDE_PLOTS:

                    create_side_by_side_plot(
                        screenshot,
                        rows,
                        paths[
                            "plots"
                        ]
                        /
                        image_filename(
                            frequency_hz,
                            rot_deg,
                            "_plot"
                        )
                    )

        # ------------------------------------------------------
        # CHECKPOINT v8:
        # cada N Rot completos.
        #
        # Cada Rot completo = len(frequencies) x 19 filas.
        # ------------------------------------------------------

        if (
            CHECKPOINT_EVERY_N_ROTATIONS
            and
            (
                r_index
                %
                CHECKPOINT_EVERY_N_ROTATIONS
                ==
                0
                or
                r_index
                ==
                len(
                    ROT_ANGLES
                )
            )
        ):

            checkpoint_df = save_rows(
                all_rows,
                paths["csv"]
            )

            print(
                "    Checkpoint Rot {:3d}°: {} filas"
                .format(
                    int(
                        rot_deg
                    ),
                    len(
                        checkpoint_df
                    )
                )
            )

    # ----------------------------------------------------------
    # Restaurar Rot 0° al terminar.
    # ----------------------------------------------------------

    try:
        set_rotation_ccw(
            directivity_hwnd,
            0
        )
    except Exception:
        pass

    # Guardado final ordenado.
    df = save_rows(
        all_rows,
        paths["csv"]
    )

    expected = (
        len(frequencies)
        *
        len(ROT_ANGLES)
        *
        19
    )

    print()
    print(
        "    CSV:",
        paths["csv"]
    )

    print(
        "    Rows:",
        len(df),
        "/",
        expected
    )

    if len(df) != expected:
        raise RuntimeError(
            "Número de filas inesperado: {} / {}".format(
                len(df),
                expected
            )
        )

    return df


# ======================================================================
# RESUMEN DE BATCH
# ======================================================================

BATCH_SUMMARY_CSV = (
    OUT_ROOT
    /
    "_batch_summary_v8.csv"
)


def append_batch_summary(
    row
):
    exists = BATCH_SUMMARY_CSV.exists()

    with BATCH_SUMMARY_CSV.open(
        "a",
        newline="",
        encoding="utf-8-sig"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=[
                "file",
                "model",
                "status",
                "rows",
                "elapsed_sec",
                "output_csv",
                "error",
            ]
        )

        if not exists:
            writer.writeheader()

        writer.writerow(
            row
        )



# ======================================================================
# AUTO-RESTART DEL PROCESO PYTHON
# ======================================================================

def load_auto_restart_count():
    try:
        if AUTO_RESTART_STATE.is_file():
            with AUTO_RESTART_STATE.open(
                "r",
                encoding="utf-8"
            ) as f:
                obj = json.load(f)

            return int(
                obj.get(
                    "restart_count",
                    0
                )
            )
    except Exception:
        pass

    return 0


def save_auto_restart_count(count):
    try:
        with AUTO_RESTART_STATE.open(
            "w",
            encoding="utf-8"
        ) as f:
            json.dump(
                {
                    "restart_count":
                        int(count)
                },
                f,
                indent=2
            )
    except Exception as exc:
        print(
            "WARNING: no pude guardar estado de auto-restart:",
            repr(exc)
        )


def reset_auto_restart_count():
    save_auto_restart_count(
        0
    )


def restart_this_python_process(
    main_hwnd,
    main_window,
    consecutive_errors
):
    """
    Hace automáticamente lo mismo que funcionó manualmente:
        parar Python
        volver a lanzar el mismo script

    NO reinicia CATT.

    Antes del exec:
      - cierra modales
      - cierra Directivity abierto
      - espera a que CATT quede habilitado

    Luego os.execv sustituye el proceso Python actual por uno nuevo.
    """

    previous_count = (
        load_auto_restart_count()
    )

    new_count = (
        previous_count
        +
        1
    )

    if (
        new_count
        >
        MAX_AUTO_RESTARTS_WITHOUT_SUCCESS
    ):
        print()
        print(
            "AUTO-RESTART CANCELADO:"
        )
        print(
            "Se alcanzó MAX_AUTO_RESTARTS_WITHOUT_SUCCESS =",
            MAX_AUTO_RESTARTS_WITHOUT_SUCCESS
        )
        print(
            "Se continuará sin reiniciar para evitar un loop infinito."
        )

        reset_auto_restart_count()

        return False

    save_auto_restart_count(
        new_count
    )

    print()
    print(
        "=" * 110
    )
    print(
        "AUTO-RESTART DE PYTHON"
    )
    print(
        "=" * 110
    )
    print(
        "Errores consecutivos:",
        consecutive_errors
    )
    print(
        "Restart:",
        new_count,
        "/",
        MAX_AUTO_RESTARTS_WITHOUT_SUCCESS
    )
    print(
        "CATT permanecerá abierto."
    )
    print(
        "Al volver, los modelos con _complete.ok serán omitidos."
    )

    # Dejar CATT en estado limpio antes de matar/reemplazar Python.
    try:
        cleanup_catt_transients()
    except Exception:
        pass

    try:
        close_directivity_windows(
            main_hwnd
        )
    except Exception:
        pass

    try:
        cleanup_catt_transients()
        wait_main_window_enabled(
            main_window,
            timeout=3.0
        )
    except Exception:
        pass

    time.sleep(
        AUTO_RESTART_DELAY_SEC
    )

    print(
        "Reiniciando:",
        sys.executable,
        " ".join(sys.argv)
    )

    # Sustituye el proceso actual. Equivale a relanzar:
    # python catt_batch_cf2_extract_v4_autorestart.py
    os.execv(
        sys.executable,
        [
            sys.executable
        ]
        +
        sys.argv
    )

    return True



# ======================================================================
# MAIN BATCH
# ======================================================================

def main():

    if not CF2_DIR.is_dir():
        raise RuntimeError(
            "No existe CF2_DIR: {}".format(
                CF2_DIR
            )
        )

    files = sorted(
        [
            p
            for p in CF2_DIR.iterdir()
            if (
                p.is_file()
                and
                p.suffix.lower()
                ==
                ".cf2"
            )
        ],
        key=lambda p:
            p.name.lower()
    )

    if MAX_FILES is not None:
        files = files[
            :MAX_FILES
        ]

    if not files:
        raise RuntimeError(
            "No encontré archivos CF2."
        )

    main_hwnd, main_title = (
        find_catt_main()
    )

    # Wrapper pywinauto de la misma ventana.
    # Se usa para los menús Window/File de CATT.
    main_window = connect_catt_main_window(
        main_hwnd
    )

    print()
    print("=" * 110)
    print("CATT BATCH CF2 EXTRACTOR")
    print("=" * 110)

    print(
        "CATT:",
        main_title
    )

    print(
        "CF2_DIR:",
        CF2_DIR
    )

    print(
        "Files:",
        len(files)
    )

    print(
        "OUT_ROOT:",
        OUT_ROOT
    )

    print(
        "Screenshots:",
        CAPTURE_SCREENSHOTS
    )

    print(
        "Overlays:",
        CREATE_OVERLAY_IMAGES
    )

    print(
        "Plots:",
        CREATE_SIDE_BY_SIDE_PLOTS
    )

    print(
        "FAST v7 timings:",
        "drag={}s".format(
            DRAG_DURATION
        ),
        "steps={}".format(
            DRAG_STEPS
        ),
        "after_drag={}s".format(
            AFTER_DRAG_DELAY
        ),
        "curve_delay={}s".format(
            AFTER_CURVE_UPDATE_DELAY
        ),
        "stable_sample={}s".format(
            ROT_STABLE_SAMPLE_DELAY_SEC
        ),
        "stable_samples={}".format(
            ROT_STABLE_REQUIRED_SAMPLES
        )
    )

    print(
        "V8 loop order:",
        "ROT -> FREQUENCY"
        if ROT_OUTER_LOOP
        else "FREQUENCY -> ROT"
    )

    print(
        "Snake frequency order:",
        SNAKE_FREQUENCY_ORDER
    )

    print(
        "Verify Rot after frequency change:",
        VERIFY_ROT_AFTER_FREQUENCY_CHANGE
    )

    print(
        "Checkpoint every N rotations:",
        CHECKPOINT_EVERY_N_ROTATIONS
    )

    calibration = load_calibration()

    print()
    print(
        "Calibration:",
        calibration
    )

    if CLOSE_EXISTING_DIRECTIVITY_AT_START:
        close_directivity_windows(
            main_hwnd
        )

    consecutive_errors = 0

    print(
        "Auto restart after consecutive errors:",
        AUTO_RESTART_AFTER_CONSECUTIVE_ERRORS
    )

    print(
        "Max auto restarts without a successful model:",
        MAX_AUTO_RESTARTS_WITHOUT_SUCCESS
    )

    for index, path in enumerate(
        files,
        start=1
    ):

        filename = path.name

        paths = create_model_dirs(
            filename
        )

        print()
        print("=" * 110)

        print(
            "[{}/{}] {}".format(
                index,
                len(files),
                filename
            )
        )

        print("=" * 110)

        if (
            SKIP_COMPLETED
            and
            paths["done"].is_file()
            and
            paths["csv"].is_file()
        ):
            print(
                "SKIP: modelo marcado como completo:"
            )

            print(
                paths["csv"]
            )

            continue

        if (
            paths["csv"].is_file()
            and
            not paths["done"].is_file()
        ):
            print(
                "INFO: existe un CSV parcial sin _complete.ok; "
                "se reprocesará el modelo desde cero."
            )

        start_time = time.time()

        status = "ERROR"
        error_text = ""
        rows_count = 0

        directivity_hwnd = None

        try:

            # El archivo anterior pudo dejar un diálogo modal aun cuando
            # su extracción haya fallado. Limpiarlo antes de tocar menús.
            cleanup_catt_transients()

            # Asegurar que no se acumulen módulos.
            if CLOSE_DIRECTIVITY_AFTER_EACH_FILE:
                close_directivity_windows(
                    main_hwnd
                )

            directivity_hwnd, loaded_title = (
                open_cf2_with_any_format(
                    main_hwnd,
                    main_window,
                    filename
                )
            )

            bring_to_front(
                directivity_hwnd
            )

            ensure_catt_directivity_maximized(
                main_hwnd,
                directivity_hwnd
            )

            df = extract_one_model(
                directivity_hwnd,
                filename,
                calibration,
                paths
            )

            rows_count = len(df)

            status = "OK"

            # Sólo un modelo completamente extraído recibe este marcador.
            with paths["done"].open(
                "w",
                encoding="utf-8"
            ) as f:
                f.write(
                    "OK\n"
                )
                f.write(
                    "file={}\n".format(
                        filename
                    )
                )
                f.write(
                    "rows={}\n".format(
                        rows_count
                    )
                )

        except KeyboardInterrupt:
            print()
            print(
                "INTERRUMPIDO POR USUARIO."
            )

            raise

        except Exception as exc:

            error_text = (
                "{}: {}".format(
                    type(exc).__name__,
                    exc
                )
            )

            print()
            print(
                "ERROR:",
                error_text
            )

            traceback.print_exc()

            # Recuperar CATT antes de seguir con el próximo archivo.
            # Si queda el selector o un popup abierto, File queda
            # deshabilitado y todos los archivos siguientes fallan.
            try:
                cleanup_catt_transients()
            except Exception:
                pass

        finally:

            elapsed = (
                time.time()
                -
                start_time
            )

            append_batch_summary({
                "file":
                    filename,
                "model":
                    paths["model"],
                "status":
                    status,
                "rows":
                    rows_count,
                "elapsed_sec":
                    round(
                        elapsed,
                        2
                    ),
                "output_csv":
                    str(
                        paths["csv"]
                    ),
                "error":
                    error_text,
            })

            # Siempre dejar CATT en estado utilizable.
            try:
                cleanup_catt_transients()
            except Exception:
                pass

            if CLOSE_DIRECTIVITY_AFTER_EACH_FILE:
                try:
                    close_directivity_windows(
                        main_hwnd
                    )
                except Exception:
                    pass

            try:
                cleanup_catt_transients()
                wait_main_window_enabled(
                    main_window,
                    timeout=2.0
                )
            except Exception:
                pass

        # ------------------------------------------------------
        # AUTO-RECUPERACIÓN ENTRE MODELOS
        # ------------------------------------------------------

        if status == "OK":

            if consecutive_errors > 0:
                print(
                    "Recuperación confirmada: "
                    "un modelo terminó correctamente."
                )

            consecutive_errors = 0

            # Un éxito demuestra que CATT/Python volvieron a un
            # estado sano; permitir nuevamente hasta N reinicios.
            reset_auto_restart_count()

        else:

            consecutive_errors += 1

            print(
                "Errores de modelo consecutivos:",
                consecutive_errors,
                "/",
                AUTO_RESTART_AFTER_CONSECUTIVE_ERRORS
            )

            if (
                consecutive_errors
                >=
                AUTO_RESTART_AFTER_CONSECUTIVE_ERRORS
            ):

                restarted = restart_this_python_process(
                    main_hwnd,
                    main_window,
                    consecutive_errors
                )

                # Sólo llegamos aquí si se canceló el restart
                # por protección contra loop infinito.
                if not restarted:
                    consecutive_errors = 0

    print()
    print("=" * 110)
    print("BATCH FINALIZADO")
    print("=" * 110)

    print(
        "Summary:",
        BATCH_SUMMARY_CSV
    )


if __name__ == "__main__":
    main()
