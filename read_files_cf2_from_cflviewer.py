# -*- coding: utf-8 -*-

import os
import re
import json
import time
import hashlib
import traceback
import ctypes

from pathlib import Path
from ctypes import wintypes

import pandas as pd

import win32gui
import win32con

from pywinauto.controls.hwndwrapper import HwndWrapper


# ======================================================================
# CONFIGURACION
# ======================================================================

CF2_DIR = r"C:\Users\Docker\Desktop\Shared\storage\speaker_cf2"

OUT_DIR = r"C:\Users\Docker\Desktop\Shared\clf_ground_truth_files"


JSON_DIR = os.path.join(
    OUT_DIR,
    "json"
)

EA_DIR = os.path.join(
    OUT_DIR,
    "ea"
)

SCREENSHOT_DIR = os.path.join(
    OUT_DIR,
    "screenshots"
)

ERROR_DIR = os.path.join(
    OUT_DIR,
    "errors"
)


for directory in [
    OUT_DIR,
    JSON_DIR,
    EA_DIR,
    SCREENSHOT_DIR,
    ERROR_DIR,
]:
    os.makedirs(
        directory,
        exist_ok=True
    )


# ======================================================================
# BATCH
# ======================================================================
#
# Para prueba:
#
# MAX_FILES = 20
#
# Para procesar TODO:
#
# MAX_FILES = None
#
# ======================================================================

MAX_FILES = None

# Si JSON ya existe:
# False -> no volver a procesar
# True  -> reemplazar
OVERWRITE = False


# No recomiendo screenshots durante los 400+ archivos.
SAVE_SCREENSHOTS = False


# Esperas conservadoras para Windows 7
FILE_LOAD_DELAY = 1.0
PAGE_DELAY = 0.25


# ======================================================================
# CONSTANTES WIN32
# ======================================================================

WM_SETTEXT = 0x000C
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111

BM_CLICK = 0x00F5
BM_GETCHECK = 0x00F0

MF_BYPOSITION = 0x00000400


# ======================================================================
# SendMessageW
# ======================================================================

user32 = ctypes.WinDLL(
    "user32",
    use_last_error=True
)

SendMessageW = user32.SendMessageW

SendMessageW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]

SendMessageW.restype = wintypes.LPARAM


# ======================================================================
# GRID DE FRECUENCIAS CLF
# ======================================================================

CF2_FREQS = [
    25,
    31.5,
    40,
    50,
    63,
    80,
    100,
    125,
    160,
    200,
    250,
    315,
    400,
    500,
    630,
    800,
    1000,
    1250,
    1600,
    2000,
    2500,
    3150,
    4000,
    5000,
    6300,
    8000,
    10000,
    12500,
    16000,
    20000,
]


# ======================================================================
# UTILIDADES
# ======================================================================

def safe_stem(path):

    name = os.path.splitext(
        os.path.basename(path)
    )[0]

    return re.sub(
        r'[<>:"/\\|?*]',
        "_",
        name
    )


def sha256_file(path):

    h = hashlib.sha256()

    with open(path, "rb") as f:

        while True:

            block = f.read(
                1024 * 1024
            )

            if not block:
                break

            h.update(
                block
            )

    return h.hexdigest()


# ======================================================================
# WM_GETTEXT
# ======================================================================

def wm_get_text(hwnd):

    try:

        n = SendMessageW(
            hwnd,
            WM_GETTEXTLENGTH,
            0,
            0
        )

        buf = ctypes.create_unicode_buffer(
            int(n) + 1
        )

        SendMessageW(
            hwnd,
            WM_GETTEXT,
            len(buf),
            ctypes.addressof(buf)
        )

        return buf.value

    except Exception:
        return ""


# ======================================================================
# WM_SETTEXT
# ======================================================================

def wm_set_text(hwnd, text):

    buf = ctypes.create_unicode_buffer(
        str(text)
    )

    SendMessageW(
        hwnd,
        WM_SETTEXT,
        0,
        ctypes.addressof(buf)
    )


# ======================================================================
# ENCONTRAR TODAS LAS VENTANAS CLF VIEWER
# ======================================================================

def find_all_clf_viewers():

    result = []

    def callback(hwnd, _):

        try:

            if not win32gui.IsWindowVisible(
                hwnd
            ):
                return

            title = win32gui.GetWindowText(
                hwnd
            )

            if (
                title
                and
                "CLF reader/viewer" in title
            ):

                result.append(
                    (
                        hwnd,
                        title
                    )
                )

        except Exception:
            pass


    win32gui.EnumWindows(
        callback,
        None
    )

    return result


# ======================================================================
# ENCONTRAR VIEWER POR ARCHIVO
# ======================================================================

def find_viewer_for_filename(
    filename
):

    target = filename.lower()

    for hwnd, title in find_all_clf_viewers():

        if target in title.lower():
            return hwnd, title

    return None


# ======================================================================
# ENCONTRAR UN VIEWER
# ======================================================================

def find_clf_viewer():

    viewers = find_all_clf_viewers()

    if not viewers:

        raise RuntimeError(
            "No encontré CLF Viewer abierto."
        )

    # Preferir ventana foreground si es un Viewer
    foreground = win32gui.GetForegroundWindow()

    for hwnd, title in viewers:

        if hwnd == foreground:
            return hwnd, title

    return viewers[0]


# ======================================================================
# ENUMERAR CONTROLES VISIBLES DE UNA VENTANA
# ======================================================================

def enum_visible_children(
    parent_hwnd
):

    rows = []

    def callback(hwnd, _):

        try:

            if not win32gui.IsWindowVisible(
                hwnd
            ):
                return

            cls = win32gui.GetClassName(
                hwnd
            )

            text = wm_get_text(
                hwnd
            )

            left, top, right, bottom = (
                win32gui.GetWindowRect(
                    hwnd
                )
            )

            rows.append({
                "hwnd":
                    hwnd,

                "class":
                    cls,

                "text":
                    text,

                "left":
                    left,

                "top":
                    top,

                "right":
                    right,

                "bottom":
                    bottom,

                "width":
                    right - left,

                "height":
                    bottom - top,
            })

        except Exception:
            pass


    win32gui.EnumChildWindows(
        parent_hwnd,
        callback,
        None
    )

    return rows


# ======================================================================
# BOTONES DE UN DIALOGO
# ======================================================================

def get_dialog_buttons(hwnd):

    result = []

    for c in enum_visible_children(
        hwnd
    ):

        if c["class"] != "Button":
            continue

        text = (
            c["text"]
            .replace("&", "")
            .strip()
            .lower()
        )

        result.append(
            text
        )

    return result


# ======================================================================
# TODOS LOS DIALOGOS CLF
# ======================================================================

def find_clf_dialogs():

    dialogs = []

    def callback(hwnd, _):

        try:

            if not win32gui.IsWindowVisible(
                hwnd
            ):
                return

            if (
                win32gui.GetClassName(hwnd)
                !=
                "#32770"
            ):
                return

            title = win32gui.GetWindowText(
                hwnd
            )

            if (
                "Open a CLF binary-file"
                not in title
            ):
                return

            dialogs.append(
                hwnd
            )

        except Exception:
            pass


    win32gui.EnumWindows(
        callback,
        None
    )

    return dialogs


# ======================================================================
# DIALOGO GRANDE OPEN
# ======================================================================

def find_open_dialog():

    for hwnd in find_clf_dialogs():

        buttons = get_dialog_buttons(
            hwnd
        )

        if (
            "open" in buttons
            and
            "cancel" in buttons
        ):
            return hwnd

    return None


# ======================================================================
# POPUP "FILE NAME IS NOT VALID"
# ======================================================================

def find_filename_error_dialog():

    for hwnd in find_clf_dialogs():

        buttons = get_dialog_buttons(
            hwnd
        )

        if "ok" in buttons:
            return hwnd

    return None


# ======================================================================
# CERRAR POPUP DE ERROR
# ======================================================================

def close_filename_error():

    hwnd = find_filename_error_dialog()

    if hwnd is None:
        return False


    print(
        "Cerrando popup de nombre de archivo..."
    )


    for c in enum_visible_children(
        hwnd
    ):

        if c["class"] != "Button":
            continue

        text = (
            c["text"]
            .replace("&", "")
            .strip()
            .lower()
        )

        if text == "ok":

            win32gui.SendMessage(
                c["hwnd"],
                BM_CLICK,
                0,
                0
            )

            time.sleep(
                0.20
            )

            return True


    try:

        win32gui.PostMessage(
            hwnd,
            WM_CLOSE,
            0,
            0
        )

    except Exception:
        pass


    return True


# ======================================================================
# CERRAR DIALOGO OPEN
# ======================================================================

def close_open_dialog():

    close_filename_error()

    hwnd = find_open_dialog()

    if hwnd is None:
        return


    for c in enum_visible_children(
        hwnd
    ):

        if c["class"] != "Button":
            continue

        text = (
            c["text"]
            .replace("&", "")
            .strip()
            .lower()
        )

        if text == "cancel":

            win32gui.SendMessage(
                c["hwnd"],
                BM_CLICK,
                0,
                0
            )

            time.sleep(
                0.25
            )

            return


    try:

        win32gui.PostMessage(
            hwnd,
            WM_CLOSE,
            0,
            0
        )

    except Exception:
        pass


# ======================================================================
# LIMPIAR TODOS LOS DIALOGOS RESIDUALES
# ======================================================================

def cleanup_dialogs():

    for _ in range(10):

        changed = False

        if find_filename_error_dialog() is not None:

            close_filename_error()

            changed = True


        if find_open_dialog() is not None:

            close_open_dialog()

            changed = True


        if not changed:
            break

        time.sleep(
            0.1
        )


# ======================================================================
# CERRAR VIEWERS EXTRAS
#
# Mantiene SOLO preferred_hwnd
# ======================================================================

def close_extra_viewers(
    preferred_hwnd=None
):

    cleanup_dialogs()


    viewers = find_all_clf_viewers()

    if len(viewers) <= 1:

        if viewers:
            return viewers[0][0]

        return None


    handles = [
        hwnd
        for hwnd, _
        in viewers
    ]


    # elegir qué ventana conservar
    if (
        preferred_hwnd is not None
        and
        preferred_hwnd in handles
    ):

        keep = preferred_hwnd

    else:

        foreground = (
            win32gui.GetForegroundWindow()
        )

        if foreground in handles:
            keep = foreground

        else:
            keep = handles[0]


    print()
    print(
        "CLF Viewers encontrados:",
        len(handles)
    )

    print(
        "Conservando HWND:",
        keep
    )


    for hwnd, title in viewers:

        if hwnd == keep:
            continue

        print(
            "Cerrando Viewer extra:",
            title
        )

        try:

            win32gui.PostMessage(
                hwnd,
                WM_CLOSE,
                0,
                0
            )

        except Exception:
            pass


    deadline = (
        time.time()
        +
        5
    )

    while time.time() < deadline:

        remaining = (
            find_all_clf_viewers()
        )

        if len(remaining) <= 1:
            break

        time.sleep(
            0.1
        )


    return keep


# ======================================================================
# BUSCAR COMANDO DEL MENU RECURSIVAMENTE
# ======================================================================

def normalize_menu_text(text):

    return (
        str(text)
        .replace("&", "")
        .split("\t")[0]
        .strip()
        .lower()
    )


def find_menu_command(
    hmenu,
    wanted_text
):

    wanted = wanted_text.lower()

    try:

        count = win32gui.GetMenuItemCount(
            hmenu
        )

    except Exception:

        return None


    for pos in range(count):

        try:

            text = win32gui.GetMenuString(
                hmenu,
                pos,
                MF_BYPOSITION
            )

        except Exception:

            text = ""


        normalized = normalize_menu_text(
            text
        )


        sub = win32gui.GetSubMenu(
            hmenu,
            pos
        )


        if sub:

            found = find_menu_command(
                sub,
                wanted_text
            )

            if found is not None:
                return found


        if wanted in normalized:

            cmd = win32gui.GetMenuItemID(
                hmenu,
                pos
            )

            if cmd not in (
                -1,
                0xFFFFFFFF
            ):

                return (
                    cmd,
                    text
                )


    return None


# ======================================================================
# ABRIR MENU "Open Distribution Binary..."
#
# IMPORTANTE:
# NO USA Ctrl+B
# ======================================================================

def invoke_open_distribution_binary(
    main_hwnd
):

    hmenu = win32gui.GetMenu(
        main_hwnd
    )


    result = find_menu_command(
        hmenu,
        "open distribution binary"
    )


    if result is not None:

        command_id, menu_text = result

        print(
            "Menu command:",
            repr(menu_text),
            "ID:",
            command_id
        )


        win32gui.SendMessage(
            main_hwnd,
            WM_COMMAND,
            command_id,
            0
        )

        return


    # Fallback sin Ctrl+B
    print(
        "Menu ID no encontrado. "
        "Usando menu_select fallback..."
    )


    viewer = HwndWrapper(
        main_hwnd
    )


    viewer.menu_select(
        "File->Open Distribution Binary..."
    )


# ======================================================================
# FILE NAME EDIT
# ======================================================================

def find_filename_edit(
    dialog_hwnd
):

    edits = []


    for c in enum_visible_children(
        dialog_hwnd
    ):

        if c["class"] != "Edit":
            continue

        if not win32gui.IsWindowEnabled(
            c["hwnd"]
        ):
            continue

        edits.append(
            c
        )


    if not edits:
        return None


    # Search box está arriba.
    # File name está abajo.
    result = max(
        edits,
        key=lambda c:
            c["top"]
    )


    return result[
        "hwnd"
    ]


# ======================================================================
# BOTON OPEN
# ======================================================================

def find_open_button(
    dialog_hwnd
):

    for c in enum_visible_children(
        dialog_hwnd
    ):

        if c["class"] != "Button":
            continue

        text = (
            c["text"]
            .replace("&", "")
            .strip()
            .lower()
        )

        if text == "open":

            return c[
                "hwnd"
            ]


    return None


# ======================================================================
# ABRIR UN CF2
#
# NO:
#   Ctrl+B
#
# SI:
#   WM_COMMAND -> File -> Open Distribution Binary
#
# ======================================================================

def open_cf2(
    main_hwnd,
    cf2_path
):

    cf2_path = os.path.abspath(
        cf2_path
    )

    filename = os.path.basename(
        cf2_path
    )


    print()
    print(
        "Opening:",
        cf2_path
    )


    if not os.path.isfile(
        cf2_path
    ):

        raise RuntimeError(
            "No existe archivo: "
            +
            cf2_path
        )


    # ------------------------------------------------------------
    # Limpiar cualquier diálogo viejo
    # ------------------------------------------------------------

    cleanup_dialogs()


    # ------------------------------------------------------------
    # Asegurar UN SOLO Viewer
    # ------------------------------------------------------------

    main_hwnd = close_extra_viewers(
        preferred_hwnd=main_hwnd
    )


    if main_hwnd is None:

        raise RuntimeError(
            "CLF Viewer dejó de existir."
        )


    # ------------------------------------------------------------
    # Llevar al frente
    # ------------------------------------------------------------

    try:

        win32gui.ShowWindow(
            main_hwnd,
            win32con.SW_RESTORE
        )

        win32gui.SetForegroundWindow(
            main_hwnd
        )

    except Exception:
        pass


    time.sleep(
        0.15
    )


    # ------------------------------------------------------------
    # ABRIR MENU
    #
    # SIN CTRL+B
    # ------------------------------------------------------------

    invoke_open_distribution_binary(
        main_hwnd
    )


    # ------------------------------------------------------------
    # Esperar diálogo
    # ------------------------------------------------------------

    dialog_hwnd = None

    deadline = (
        time.time()
        +
        8
    )


    while time.time() < deadline:

        dialog_hwnd = (
            find_open_dialog()
        )

        if dialog_hwnd is not None:
            break

        time.sleep(
            0.1
        )


    if dialog_hwnd is None:

        raise RuntimeError(
            "No apareció diálogo "
            "Open a CLF binary-file."
        )


    print(
        "Dialog HWND:",
        dialog_hwnd
    )


    # ------------------------------------------------------------
    # File name
    # ------------------------------------------------------------

    edit_hwnd = find_filename_edit(
        dialog_hwnd
    )


    if edit_hwnd is None:

        close_open_dialog()

        raise RuntimeError(
            "No encontré File name."
        )


    print(
        "File name HWND:",
        edit_hwnd
    )


    # ------------------------------------------------------------
    # Escribir ruta completa
    # ------------------------------------------------------------

    wm_set_text(
        edit_hwnd,
        cf2_path
    )


    time.sleep(
        0.15
    )


    written = wm_get_text(
        edit_hwnd
    )


    print(
        "File name contains:",
        repr(written)
    )


    if (
        written.strip().lower()
        !=
        cf2_path.strip().lower()
    ):

        print(
            "Expected:",
            repr(cf2_path)
        )

        print(
            "Actual:",
            repr(written)
        )

        close_open_dialog()

        raise RuntimeError(
            "La ruta no quedó escrita correctamente."
        )


    # ------------------------------------------------------------
    # Open
    # ------------------------------------------------------------

    open_hwnd = find_open_button(
        dialog_hwnd
    )


    if open_hwnd is None:

        close_open_dialog()

        raise RuntimeError(
            "No encontré botón Open."
        )


    print(
        "Open HWND:",
        open_hwnd
    )


    win32gui.SendMessage(
        open_hwnd,
        BM_CLICK,
        0,
        0
    )


    # ------------------------------------------------------------
    # Esperar load
    # ------------------------------------------------------------

    deadline = (
        time.time()
        +
        15
    )


    while time.time() < deadline:

        # error explícito
        if (
            find_filename_error_dialog()
            is not None
        ):

            close_filename_error()

            close_open_dialog()

            raise RuntimeError(
                "CLF Viewer rechazó el archivo: "
                +
                filename
            )


        loaded = find_viewer_for_filename(
            filename
        )


        if loaded is not None:

            loaded_hwnd, title = loaded


            print(
                "Loaded:",
                title
            )


            # Si por cualquier motivo Viewer abrió otra ventana,
            # cerrar las anteriores y conservar ESTA.
            loaded_hwnd = close_extra_viewers(
                preferred_hwnd=loaded_hwnd
            )


            time.sleep(
                FILE_LOAD_DELAY
            )


            return loaded_hwnd


        time.sleep(
            0.15
        )


    cleanup_dialogs()


    raise RuntimeError(
        "Timeout cargando: "
        +
        filename
    )


# ======================================================================
# CONTROLES DEL VIEWER
# ======================================================================

def get_controls(
    main_hwnd
):

    main_left, main_top, _, _ = (
        win32gui.GetWindowRect(
            main_hwnd
        )
    )


    rows = []


    def callback(hwnd, _):

        try:

            cls = win32gui.GetClassName(
                hwnd
            )

            text = wm_get_text(
                hwnd
            )


            if not text:

                try:
                    text = win32gui.GetWindowText(
                        hwnd
                    )
                except Exception:
                    text = ""


            left, top, right, bottom = (
                win32gui.GetWindowRect(
                    hwnd
                )
            )


            rows.append({

                "hwnd":
                    int(hwnd),

                "class":
                    cls,

                "text":
                    text,

                "left":
                    left - main_left,

                "top":
                    top - main_top,

                "right":
                    right - main_left,

                "bottom":
                    bottom - main_top,

                "width":
                    right - left,

                "height":
                    bottom - top,
            })

        except Exception:
            pass


    win32gui.EnumChildWindows(
        main_hwnd,
        callback,
        None
    )


    return rows


# ======================================================================
# BUSCAR CONTROL POR POSICION
# ======================================================================

def nearest_control(
    controls,
    cls,
    left,
    top,
    tol_x=10,
    tol_y=7
):

    candidates = []


    for c in controls:

        if c["class"] != cls:
            continue


        dx = abs(
            c["left"] - left
        )

        dy = abs(
            c["top"] - top
        )


        if (
            dx <= tol_x
            and
            dy <= tol_y
        ):

            candidates.append(
                (
                    dx + dy,
                    c
                )
            )


    if not candidates:
        return None


    return min(
        candidates,
        key=lambda x:
            x[0]
    )[1]


def value_at(
    controls,
    cls,
    left,
    top,
    tol_x=10,
    tol_y=7
):

    c = nearest_control(
        controls,
        cls,
        left,
        top,
        tol_x,
        tol_y
    )


    if c is None:
        return ""


    return (
        c["text"]
        .strip()
    )


# ======================================================================
# METADATA
# ======================================================================

def extract_metadata(
    controls
):

    m = {}


    # MODEL
    m["model_name"] = value_at(
        controls, "Edit", 80, 68
    )

    m["weight_kg"] = value_at(
        controls, "Static", 452, 68
    )

    m["description"] = value_at(
        controls, "Edit", 80, 98
    )

    m["colors"] = value_at(
        controls, "Edit", 80, 126
    )

    m["radiation"] = value_at(
        controls, "Static", 80, 154
    )

    m["mounting"] = value_at(
        controls, "Edit", 216, 154
    )

    m["type"] = value_at(
        controls, "Static", 80, 182
    )

    m["model_info"] = value_at(
        controls, "Edit", 216, 182
    )


    # MANUFACTURER
    m["manufacturer"] = value_at(
        controls, "Edit", 80, 238
    )

    m["website"] = value_at(
        controls, "Button", 321, 236
    )


    # MEASUREMENT
    m["contact"] = value_at(
        controls, "Edit", 80, 298
    )

    m["email"] = value_at(
        controls, "Edit", 277, 298
    )

    m["measurement_date"] = value_at(
        controls, "Static", 80, 326
    )

    m["measurement_info"] = value_at(
        controls, "Edit", 225, 326
    )

    m["distance_m"] = value_at(
        controls, "Static", 80, 354
    )

    m["environment"] = value_at(
        controls, "Edit", 199, 354
    )


    # IMPEDANCE DERIVED
    m["impedance_nom_ohm"] = value_at(
        controls, "Static", 59, 656
    )

    m["impedance_min_ohm"] = value_at(
        controls, "Static", 134, 656
    )

    m["impedance_avg_ohm"] = value_at(
        controls, "Static", 209, 656
    )


    # MAX INPUT
    m["max_power_or_voltage"] = value_at(
        controls, "Static", 409, 642
    )

    m["max_input_unit"] = value_at(
        controls, "Static", 465, 646
    )

    m["equivalent_amp_size_w"] = value_at(
        controls, "Static", 409, 670
    )

    m["gain_db"] = value_at(
        controls, "Static", 409, 696
    )


    # localizar label dinámico
    m["max_input_label"] = ""

    for c in controls:

        text = (
            c["text"]
            .strip()
        )

        if text.lower().startswith(
            "max input "
        ):

            m["max_input_label"] = text
            break


    # CLF VERSION
    m["clf_format"] = value_at(
        controls, "Static", 11, 780
    )

    m["clf_version"] = value_at(
        controls, "Static", 102, 780
    )

    m["clf_release"] = value_at(
        controls, "Static", 130, 780
    )

    m["part"] = value_at(
        controls, "Static", 235, 780
    )

    m["parts_total"] = value_at(
        controls, "Static", 283, 780
    )


    return m


# ======================================================================
# NUMEROS DEL VIEWER
# ======================================================================

def parse_display_number(
    text
):

    if text is None:
        return None


    s = str(text).strip()


    if (
        not s
        or
        s == "-"
    ):
        return None


    s = (
        s
        .replace(",", ".")
        .strip("()")
    )


    try:
        return float(s)

    except ValueError:
        return None


# ======================================================================
# FRECUENCIA
# ======================================================================

def parse_frequency(
    text
):

    if text is None:
        return None


    s = (
        str(text)
        .strip()
        .lower()
        .replace(",", ".")
    )


    if not s:
        return None


    try:

        if s.endswith("k"):

            return (
                float(s[:-1])
                *
                1000.0
            )

        return float(s)

    except ValueError:
        return None


# ======================================================================
# EA DATA
# ======================================================================

COLUMN_LEFTS = [
    95,
    137,
    179,
    221,
    263,
    305,
    347,
    389,
    431,
]


FREQ_LEFTS = [
    x - 3
    for x in COLUMN_LEFTS
]


ROW_TOPS = {

    "input_voltage_vrms":
        382,

    "sensitivity_db":
        462,

    "impedance_ohm":
        490,

    "h_width_deg":
        520,

    "v_width_deg":
        548,

    "axial_q":
        578,

    "axial_spectrum_db":
        608,
}


def get_static_cell(
    controls,
    left,
    top
):

    c = nearest_control(
        controls,
        "Static",
        left,
        top,
        tol_x=8,
        tol_y=5
    )


    if c is None:
        return ""


    return (
        c["text"]
        .strip()
    )


# ======================================================================
# LEER LAS 9 COLUMNAS VISIBLES
# ======================================================================

def extract_ea_page(
    controls
):

    rows = []


    for i in range(9):

        freq_text = get_static_cell(
            controls,
            FREQ_LEFTS[i],
            422
        )


        freq = parse_frequency(
            freq_text
        )


        if freq is None:
            continue


        row = {

            "frequency_hz":
                freq,

            "frequency_display":
                freq_text,
        }


        for field, top in ROW_TOPS.items():

            raw = get_static_cell(
                controls,
                COLUMN_LEFTS[i],
                top
            )


            row[
                field + "_display"
            ] = raw


            row[field] = (
                parse_display_number(
                    raw
                )
            )


        rows.append(
            row
        )


    return rows


# ======================================================================
# BOTONES FRECUENCIA < >
# ======================================================================

def find_page_button(
    controls,
    text
):

    for c in controls:

        if (
            c["class"] == "Button"
            and
            c["text"].strip() == text
            and
            405 <= c["top"] <= 440
        ):

            return c


    return None


def click_hwnd(
    hwnd
):

    win32gui.SendMessage(
        hwnd,
        BM_CLICK,
        0,
        0
    )

    time.sleep(
        PAGE_DELAY
    )


# ======================================================================
# SIGNATURE PAGINA
# ======================================================================

def page_signature(
    main_hwnd
):

    page = extract_ea_page(
        get_controls(
            main_hwnd
        )
    )


    return tuple(
        r["frequency_hz"]
        for r in page
    )


# ======================================================================
# IR AL INICIO
# ======================================================================

def go_to_first_page(
    main_hwnd
):

    for _ in range(40):

        controls = get_controls(
            main_hwnd
        )


        left = find_page_button(
            controls,
            "<"
        )


        if left is None:
            break


        before = page_signature(
            main_hwnd
        )


        click_hwnd(
            left["hwnd"]
        )


        after = page_signature(
            main_hwnd
        )


        if after == before:
            break


# ======================================================================
# TODAS LAS FRECUENCIAS
# ======================================================================

def extract_all_ea(
    main_hwnd
):

    go_to_first_page(
        main_hwnd
    )


    seen_pages = set()

    unique = {}


    for viewer_position in range(40):

        controls = get_controls(
            main_hwnd
        )


        page = extract_ea_page(
            controls
        )


        signature = tuple(
            row["frequency_hz"]
            for row in page
        )


        if not signature:
            break


        if signature in seen_pages:
            break


        seen_pages.add(
            signature
        )


        for row in page:

            freq = row[
                "frequency_hz"
            ]


            if freq not in unique:

                row[
                    "viewer_position"
                ] = viewer_position


                unique[
                    freq
                ] = row


        right = find_page_button(
            controls,
            ">"
        )


        if right is None:
            break


        before = signature


        click_hwnd(
            right["hwnd"]
        )


        after = page_signature(
            main_hwnd
        )


        if after == before:
            break


    result = list(
        unique.values()
    )


    result.sort(
        key=lambda r:
            r["frequency_hz"]
    )


    return result


# ======================================================================
# STATUS BAR
# ======================================================================

def extract_status_bar(
    controls
):

    result = []


    for c in controls:

        if (
            c["class"]
            !=
            "msctls_statusbar32"
        ):
            continue


        if c["text"].strip():

            result.append(
                c["text"].strip()
            )


        try:

            texts = HwndWrapper(
                c["hwnd"]
            ).texts()

            for text in texts:

                if text:
                    result.append(
                        text
                    )

        except Exception:
            pass


    return list(
        dict.fromkeys(
            result
        )
    )


# ======================================================================
# ESTADOS VIEWER
# ======================================================================

def extract_button_states(
    controls
):

    wanted = {

        "Rectangular",
        "Trapezoidal",
        "Edges",
        "Faces+Edges",
        "DXF",

        "Polars",
        "Color maps",
        "EA data",
        "Balloon-spectra",
    }


    result = {}


    for c in controls:

        if c["class"] != "Button":
            continue


        text = (
            c["text"]
            .replace("&", "")
            .strip()
        )


        if text not in wanted:
            continue


        try:

            result[text] = int(
                win32gui.SendMessage(
                    c["hwnd"],
                    BM_GETCHECK,
                    0,
                    0
                )
            )

        except Exception:

            result[text] = None


    return result


# ======================================================================
# GUARDAR UN ARCHIVO
# ======================================================================

def save_one_ground_truth(
    cf2_path,
    main_hwnd
):

    filename = os.path.basename(
        cf2_path
    )

    stem = safe_stem(
        cf2_path
    )


    controls = get_controls(
        main_hwnd
    )


    metadata = extract_metadata(
        controls
    )


    metadata[
        "source_file"
    ] = filename


    metadata[
        "source_path"
    ] = cf2_path


    metadata[
        "sha256"
    ] = sha256_file(
        cf2_path
    )


    metadata[
        "viewer_window_title"
    ] = win32gui.GetWindowText(
        main_hwnd
    )


    metadata[
        "status_bar"
    ] = extract_status_bar(
        controls
    )


    metadata[
        "viewer_state"
    ] = extract_button_states(
        controls
    )


    # ------------------------------------------------------------
    # EA
    # ------------------------------------------------------------

    ea_rows = extract_all_ea(
        main_hwnd
    )


    for row in ea_rows:

        row[
            "source_file"
        ] = filename

        row[
            "model_name"
        ] = metadata.get(
            "model_name",
            ""
        )


    frequencies = [
        r["frequency_hz"]
        for r in ea_rows
    ]


    validation = {

        "model_found":
            bool(
                metadata.get(
                    "model_name"
                )
            ),

        "n_frequencies":
            len(
                ea_rows
            ),

        "first_frequency_hz":
            (
                frequencies[0]
                if frequencies
                else None
            ),

        "last_frequency_hz":
            (
                frequencies[-1]
                if frequencies
                else None
            ),

        "frequencies_sorted":
            (
                frequencies
                ==
                sorted(frequencies)
            ),

        "frequencies_unique":
            (
                len(frequencies)
                ==
                len(set(frequencies))
            ),

        "frequencies_on_clf_grid":
            all(
                f in CF2_FREQS
                for f in frequencies
            ),
    }


    result = {

        "metadata":
            metadata,

        "validation":
            validation,

        "ea_data":
            ea_rows,
    }


    # ------------------------------------------------------------
    # JSON
    # ------------------------------------------------------------

    json_path = os.path.join(
        JSON_DIR,
        stem + ".json"
    )


    with open(
        json_path,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            result,
            f,
            ensure_ascii=False,
            indent=2
        )


    # ------------------------------------------------------------
    # CSV individual
    # ------------------------------------------------------------

    if ea_rows:

        pd.DataFrame(
            ea_rows
        ).to_csv(
            os.path.join(
                EA_DIR,
                stem + ".csv"
            ),
            index=False,
            encoding="utf-8-sig"
        )


    # ------------------------------------------------------------
    # Screenshot opcional
    # ------------------------------------------------------------

    if SAVE_SCREENSHOTS:

        try:

            image = HwndWrapper(
                main_hwnd
            ).capture_as_image()


            image.save(
                os.path.join(
                    SCREENSHOT_DIR,
                    stem + ".png"
                )
            )

        except Exception as e:

            print(
                "Screenshot ERROR:",
                repr(e)
            )


    return result


# ======================================================================
# OBTENER CF2
# ======================================================================

def get_cf2_files(
    folder
):

    print()
    print(
        "CF2_DIR:",
        repr(folder)
    )


    print(
        "exists:",
        os.path.exists(
            folder
        )
    )


    print(
        "isdir:",
        os.path.isdir(
            folder
        )
    )


    if not os.path.isdir(
        folder
    ):

        raise RuntimeError(
            "No existe CF2_DIR:\n"
            +
            folder
        )


    files = []


    for name in os.listdir(
        folder
    ):

        full = os.path.join(
            folder,
            name
        )


        if (
            os.path.isfile(full)
            and
            name.lower().endswith(
                ".cf2"
            )
        ):

            files.append(
                full
            )


    files.sort(
        key=lambda x:
            os.path.basename(x).lower()
    )


    print(
        "CF2 encontrados:",
        len(files)
    )


    return files


# ======================================================================
# ERRORES
# ======================================================================

def save_error(
    record
):

    path = os.path.join(
        OUT_DIR,
        "errors.csv"
    )


    if os.path.exists(path):

        try:

            old = pd.read_csv(
                path
            )

        except Exception:

            old = pd.DataFrame()

    else:

        old = pd.DataFrame()


    new = pd.DataFrame(
        [record]
    )


    result = pd.concat(
        [
            old,
            new
        ],
        ignore_index=True
    )


    if "file" in result.columns:

        result = result.drop_duplicates(
            subset=[
                "file"
            ],
            keep="last"
        )


    result.to_csv(
        path,
        index=False,
        encoding="utf-8-sig"
    )


# ======================================================================
# RECONSTRUIR MASTER FILES
# ======================================================================

def rebuild_master_files():

    metadata_rows = []

    ea_rows = []


    for path in sorted(
        Path(JSON_DIR).glob(
            "*.json"
        )
    ):

        try:

            with open(
                str(path),
                "r",
                encoding="utf-8"
            ) as f:

                data = json.load(
                    f
                )


            metadata = dict(
                data.get(
                    "metadata",
                    {}
                )
            )


            validation = data.get(
                "validation",
                {}
            )


            for key, value in validation.items():

                metadata[
                    "validation_" + key
                ] = value


            if isinstance(
                metadata.get(
                    "status_bar"
                ),
                list
            ):

                metadata[
                    "status_bar"
                ] = json.dumps(
                    metadata[
                        "status_bar"
                    ],
                    ensure_ascii=False
                )


            if isinstance(
                metadata.get(
                    "viewer_state"
                ),
                dict
            ):

                metadata[
                    "viewer_state"
                ] = json.dumps(
                    metadata[
                        "viewer_state"
                    ],
                    ensure_ascii=False
                )


            metadata_rows.append(
                metadata
            )


            ea_rows.extend(
                data.get(
                    "ea_data",
                    []
                )
            )


        except Exception as e:

            print(
                "Error leyendo JSON:",
                path.name,
                repr(e)
            )


    # MASTER METADATA
    if metadata_rows:

        df_meta = pd.DataFrame(
            metadata_rows
        )


        if (
            "source_file"
            in df_meta.columns
        ):

            df_meta = df_meta.sort_values(
                "source_file"
            )


        df_meta.to_csv(
            os.path.join(
                OUT_DIR,
                "master_metadata.csv"
            ),
            index=False,
            encoding="utf-8-sig"
        )


    # MASTER EA
    if ea_rows:

        df_ea = pd.DataFrame(
            ea_rows
        )


        if (
            "source_file"
            in df_ea.columns
            and
            "frequency_hz"
            in df_ea.columns
        ):

            df_ea = df_ea.sort_values(
                [
                    "source_file",
                    "frequency_hz"
                ]
            )


        df_ea.to_csv(
            os.path.join(
                OUT_DIR,
                "master_ea.csv"
            ),
            index=False,
            encoding="utf-8-sig"
        )


# ======================================================================
# ARCHIVOS YA TERMINADOS
# ======================================================================

def get_completed_source_files():

    result = set()


    for path in Path(
        JSON_DIR
    ).glob(
        "*.json"
    ):

        try:

            with open(
                str(path),
                "r",
                encoding="utf-8"
            ) as f:

                data = json.load(
                    f
                )


            source = (
                data.get(
                    "metadata",
                    {}
                )
                .get(
                    "source_file"
                )
            )


            if source:
                result.add(
                    source
                )

        except Exception:
            pass


    return result


# ======================================================================
# REPORTE FALTANTES
# ======================================================================

def build_missing_report(
    files
):

    completed = (
        get_completed_source_files()
    )


    missing = []


    for path in files:

        filename = os.path.basename(
            path
        )


        if filename not in completed:

            missing.append({
                "file":
                    filename,

                "path":
                    path,
            })


    missing_path = os.path.join(
        OUT_DIR,
        "missing_files.csv"
    )


    pd.DataFrame(
        missing,
        columns=[
            "file",
            "path"
        ]
    ).to_csv(
        missing_path,
        index=False,
        encoding="utf-8-sig"
    )


    return completed, missing


# ======================================================================
# BATCH
# ======================================================================

def run_batch():

    print()
    print(
        "=" * 110
    )

    print(
        "CLF VIEWER GROUND TRUTH BATCH"
    )

    print(
        "=" * 110
    )


    # ------------------------------------------------------------------
    # Primero limpiar popups
    # ------------------------------------------------------------------

    cleanup_dialogs()


    # ------------------------------------------------------------------
    # Cerrar todas las ventanas CLF extras
    # ------------------------------------------------------------------

    viewers = find_all_clf_viewers()


    if not viewers:

        raise RuntimeError(
            "Abre CLF Viewer manualmente una vez "
            "antes de ejecutar el script."
        )


    main_hwnd, title = find_clf_viewer()


    main_hwnd = close_extra_viewers(
        preferred_hwnd=main_hwnd
    )


    main_hwnd, title = find_clf_viewer()


    print()
    print(
        "Viewer conservado:",
        title
    )


    print(
        "Viewers abiertos ahora:",
        len(
            find_all_clf_viewers()
        )
    )


    # ------------------------------------------------------------------
    # Archivos
    # ------------------------------------------------------------------

    all_files = get_cf2_files(
        CF2_DIR
    )


    files = all_files


    if MAX_FILES is not None:

        files = files[
            :MAX_FILES
        ]


    print()
    print(
        "Archivos en corpus:",
        len(
            all_files
        )
    )


    print(
        "Archivos en esta ejecución:",
        len(
            files
        )
    )


    # ------------------------------------------------------------------
    # Batch
    # ------------------------------------------------------------------

    for index, cf2_path in enumerate(
        files,
        start=1
    ):

        filename = os.path.basename(
            cf2_path
        )


        stem = safe_stem(
            cf2_path
        )


        json_path = os.path.join(
            JSON_DIR,
            stem + ".json"
        )


        print()
        print(
            "=" * 110
        )

        print(
            "[{}/{}] {}".format(
                index,
                len(files),
                filename
            )
        )

        print(
            "=" * 110
        )


        # --------------------------------------------------------------
        # RESUME
        # --------------------------------------------------------------

        if (
            not OVERWRITE
            and
            os.path.exists(
                json_path
            )
        ):

            print(
                "SKIP: ya existe ground truth."
            )

            continue


        try:

            main_hwnd = open_cf2(
                main_hwnd,
                cf2_path
            )


            result = save_one_ground_truth(
                cf2_path,
                main_hwnd
            )


            validation = result[
                "validation"
            ]


            print(
                "Model:",
                result[
                    "metadata"
                ].get(
                    "model_name",
                    ""
                )
            )


            print(
                "Bands:",
                validation[
                    "n_frequencies"
                ]
            )


            print(
                "Range:",
                validation[
                    "first_frequency_hz"
                ],
                "->",
                validation[
                    "last_frequency_hz"
                ],
                "Hz"
            )


            completed = len(
                list(
                    Path(
                        JSON_DIR
                    ).glob(
                        "*.json"
                    )
                )
            )


            print(
                "Ground truths:",
                completed
            )


            print(
                "Viewers abiertos:",
                len(
                    find_all_clf_viewers()
                )
            )


            # debe quedar siempre 1
            main_hwnd = close_extra_viewers(
                preferred_hwnd=main_hwnd
            )


        except Exception as e:

            print()
            print(
                "ERROR:",
                repr(e)
            )


            traceback.print_exc()


            cleanup_dialogs()


            # asegurar que un error no deje varias ventanas
            viewers = find_all_clf_viewers()


            if viewers:

                preferred = (
                    main_hwnd
                    if any(
                        hwnd == main_hwnd
                        for hwnd, _
                        in viewers
                    )
                    else viewers[0][0]
                )


                main_hwnd = close_extra_viewers(
                    preferred_hwnd=preferred
                )


            error_record = {

                "file":
                    filename,

                "path":
                    cf2_path,

                "error":
                    repr(e),

                "traceback":
                    traceback.format_exc(),

                "timestamp":
                    time.strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
            }


            save_error(
                error_record
            )


            time.sleep(
                0.5
            )


            continue


    # ------------------------------------------------------------------
    # Masters
    # ------------------------------------------------------------------

    rebuild_master_files()


    completed, missing = (
        build_missing_report(
            all_files
        )
    )


    # ------------------------------------------------------------------
    # Limpieza final: una sola ventana
    # ------------------------------------------------------------------

    viewers = find_all_clf_viewers()


    if viewers:

        main_hwnd = close_extra_viewers(
            preferred_hwnd=main_hwnd
        )


    print()
    print(
        "=" * 110
    )

    print(
        "BATCH FINALIZADO"
    )

    print(
        "=" * 110
    )


    print(
        "CF2 encontrados:",
        len(
            all_files
        )
    )


    print(
        "Ground truths completos:",
        len(
            completed
        )
    )


    print(
        "Faltantes:",
        len(
            missing
        )
    )


    print(
        "Viewers abiertos:",
        len(
            find_all_clf_viewers()
        )
    )


    print(
        "Output:",
        OUT_DIR
    )


# ======================================================================
# MAIN
# ======================================================================

if __name__ == "__main__":

    run_batch()