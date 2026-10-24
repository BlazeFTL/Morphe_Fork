#!/usr/bin/env python3
"""
patch.py -- apply fork changes to a MorpheApp/morphe-manager checkout (dev branch):
  1. favorite universal patches (star + long-press, favorites float to top)
  2. expert-gated "Skip APK signing" (morphe-manager PR #1008), a row under the Expert mode
     switch in Settings > Patching, shown and honored only while expert mode is on
  3. fork identity (applicationId, app name)
  4. "select APK" button moved out of the apps toolbar to a labelled pill button beside the
     bottom navigation pill, always visible from first frame
  5. Settings > Updates: patch update check at launch limited to every 3/6/12/24 hours,
     picked in a radio dialog
  6. Sources page header: search toggle moved ahead of the actions, order Search, Sort, Add (+)
  7. Expert patch dialog: bundles with a patch of their own for the app (not only universal
     ones) are listed first
  8. Patcher log: the loader's "Universal patches must be declared with default false" notice
     (header, message and the indented bundle/patch list) is dropped

Anchors are structural (function bodies, call arguments, parameter lists, single
lines), never pasted multi-line context. Files are found by declaration; the path
is only a hint. Feature code lives in new files. Idempotent and all-or-nothing:
nothing is written unless every required edit resolves and the result passes
bracket and reference checks.

Usage:
    python3 patch.py [path-to-morphe-manager-checkout]
    python3 patch.py [path] --check     dry run, exit 1 if anything is pending
"""
import argparse
import os
import pathlib
import re
import sys
import textwrap
from collections import namedtuple

FORK_APP_ID = "app.morphe.manages"
FORK_APP_NAME = "MorpheFork"
FORK_REPO = os.environ.get("FORK_REPO", "").strip()
DEFAULT_UPSTREAM_APP_ID = "app.morphe.manager"

BOM = "\ufeff"
NOTES = []

Fun = namedtuple("Fun", "start name lp rp bo bc eq")


def note(msg):
    NOTES.append(msg)


def mask(src):
    out = list(src)
    n = len(src)

    def blank(a, b):
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    i = 0
    while i < n:
        c = src[i]
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
        elif src.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if src.startswith("/*", j):
                    depth += 1
                    j += 2
                elif src.startswith("*/", j):
                    depth -= 1
                    j += 2
                else:
                    j += 1
            blank(i, j)
            i = j
        elif src.startswith('"""', i):
            j = src.find('"""', i + 3)
            j = n if j < 0 else j + 3
            blank(i, j)
            i = j
        elif c == '"':
            j = i + 1
            while j < n and src[j] != '"' and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            blank(i, j + 1)
            i = j + 1
        elif c == "'":
            mm = re.match(r"'(?:\\u[0-9a-fA-F]{4}|\\.|[^\\'\n])'", src[i:i + 8])
            if mm:
                blank(i, i + mm.end())
                i += mm.end()
            else:
                i += 1
        else:
            i += 1
    return "".join(out)


PAIRS = {"(": ")", "{": "}", "[": "]"}


def match_close(m, i):
    open_c = m[i]
    close_c = PAIRS[open_c]
    depth = 0
    for j in range(i, len(m)):
        if m[j] == open_c:
            depth += 1
        elif m[j] == close_c:
            depth -= 1
            if depth == 0:
                return j
    return -1


def balanced(m):
    stack = []
    for c in m:
        if c in PAIRS:
            stack.append(PAIRS[c])
        elif c in ")}]":
            if not stack or stack.pop() != c:
                return False
    return not stack


def line_start(s, i):
    return s.rfind("\n", 0, i) + 1


def line_end(s, i):
    j = s.find("\n", i)
    return len(s) if j < 0 else j


def indent_of(s, i):
    return re.match(r"[ \t]*", s[line_start(s, i):]).group(0)


def reindent(text, ind):
    return "\n".join(ind + l if l.strip() else l for l in text.split("\n"))


def split_top(text):
    parts, depth, last = [], 0, 0
    for i, c in enumerate(text):
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            parts.append(text[last:i])
            last = i + 1
    parts.append(text[last:])
    return parts


def annotation_start(src, pos):
    ls = line_start(src, pos)
    while ls > 0:
        pls = line_start(src, ls - 1)
        if src[pls:ls].strip().startswith("@"):
            ls = pls
        else:
            break
    return ls


def doc_start(src, pos):
    ls = annotation_start(src, pos)
    if ls > 0:
        pls = line_start(src, ls - 1)
        if src[pls:ls].strip().endswith("*/"):
            k = src.rfind("/*", 0, ls)
            if k >= 0:
                return line_start(src, k)
    return ls


_FUN_RX = re.compile(r"\bfun\s+(?:<[^>\n]*>\s*)?(?:[\w.]+\.)?(\w+)\s*\(")
_AFTER_PARAMS = re.compile(r"\s*(?::[^{=]*)?(\{|=)")


def all_funs(m):
    out = []
    for mm in _FUN_RX.finditer(m):
        lp = mm.end() - 1
        rp = match_close(m, lp)
        if rp < 0:
            continue
        bo = bc = eq = None
        t = _AFTER_PARAMS.match(m, rp + 1)
        if t:
            if t.group(1) == "{":
                bo = t.end() - 1
                bc = match_close(m, bo)
            else:
                eq = t.end() - 1
        out.append(Fun(mm.start(), mm.group(1), lp, rp, bo, bc, eq))
    return out


def find_funs(m, name):
    return [f for f in all_funs(m) if f.name == name]


def one(items):
    return items[0] if len(items) == 1 else None


def fun_end(m, f):
    if f.bo is not None:
        return f.bc
    k = m.find("{", f.eq)
    nl = m.find("\n", f.eq)
    if k != -1 and (nl == -1 or k < nl):
        return match_close(m, k)
    return nl if nl != -1 else len(m) - 1


def enclosing_fun(m, pos):
    best = None
    for f in all_funs(m):
        if f.bo is not None and f.bo < pos < f.bc:
            if best is None or f.bo > best.bo:
                best = f
    return best


def find_calls(m, name, lo=0, hi=None):
    hi = len(m) if hi is None else hi
    out = []
    for mm in re.finditer(r"(?<![\w.])" + re.escape(name) + r"\s*\(", m[lo:hi]):
        s = lo + mm.start()
        if re.search(r"(?:^|\W)fun\s+$", m[max(0, s - 8):s]):
            continue
        out.append(lo + mm.end() - 1)
    return out


def anchor(m, rx, which=None):
    hits = list(re.finditer(rx, m, re.M))
    if which is None:
        return hits[0] if len(hits) == 1 else None
    return hits[which] if len(hits) > which else None


def insert_after_line(src, m, rx, text, which=None, extra=""):
    h = anchor(m, rx, which)
    if not h:
        return None
    ind = indent_of(src, h.start()) + extra
    e = line_end(src, h.end())
    return src[:e] + "\n" + reindent(text, ind) + src[e:]


def insert_before_line(src, m, rx, text, which=None):
    h = anchor(m, rx, which)
    if not h:
        return None
    s = line_start(src, h.start())
    ind = indent_of(src, h.start())
    return src[:s] + reindent(text, ind) + "\n" + src[s:]


def add_first_arg(src, m, lp, text, marker=None):
    rp = match_close(m, lp)
    if rp < 0:
        return None
    if marker and re.search(marker, m[lp:rp]):
        return src
    mm = re.match(r"(?:[ \t]*\n)+", src[lp + 1:rp])
    if not mm:
        return None
    s = lp + 1 + mm.end()
    ind = indent_of(src, s)
    return src[:s] + reindent(text, ind) + "\n" + src[s:]


def add_last_param(src, m, f, text, marker):
    if re.search(marker, m[f.lp:f.rp]):
        return src
    region = m[f.lp + 1:f.rp]
    end = len(region.rstrip())
    ls = region.rfind("\n", 0, end) + 1
    if ls == 0 and "\n" not in region[:end]:
        return None
    last = region[ls:end]
    if not re.match(r"\s*(?:vararg\s+|noinline\s+|crossinline\s+)?\w+\s*:", last):
        return None
    s = f.lp + 1 + ls
    ind = indent_of(src, s)
    return src[:s] + reindent(text, ind) + "\n" + src[s:]


def package_of(text):
    mm = re.search(r"^package\s+([\w.]+)", text, re.M)
    return mm.group(1) if mm else None


def ensure_import(src, fq):
    if re.search(r"^import\s+" + re.escape(fq) + r"\s*$", src, re.M):
        return src
    pkg, _, _ = fq.rpartition(".")
    if package_of(src) == pkg:
        return src
    if re.search(r"^import\s+" + re.escape(pkg) + r"\.\*\s*$", src, re.M):
        return src
    lines = src.split("\n")
    imports = [i for i, l in enumerate(lines) if l.startswith("import ")]
    line = "import " + fq
    if not imports:
        pk = next((i for i, l in enumerate(lines) if l.startswith("package ")), None)
        if pk is None:
            return None
        lines[pk + 1:pk + 1] = ["", line]
        return "\n".join(lines)
    for i in imports:
        if lines[i][7:].strip() > fq:
            lines.insert(i, line)
            return "\n".join(lines)
    lines.insert(imports[-1] + 1, line)
    return "\n".join(lines)


def imp(fq):
    return lambda src: ensure_import(src, fq)


def sub_once(src, rx, repl, flags=0):
    hits = list(re.finditer(rx, src, flags))
    if len(hits) != 1:
        return None
    h = hits[0]
    return src[:h.start()] + h.expand(repl) + src[h.end():]


# ---------------------------------------------------------------- resources / gradle

def strings_edit(entries, anchor_name):
    def fn(src):
        missing = [x for n, x in entries if 'name="%s"' % n not in src]
        if not missing:
            return src
        h = re.search(r'^([ \t]*)<string\s+name="%s"[^\n]*</string>[ \t]*$' % re.escape(anchor_name), src, re.M)
        if h:
            block = "\n".join(h.group(1) + x for x in missing)
            return src[:h.end()] + "\n" + block + src[h.end():]
        k = src.rfind("</resources>")
        if k < 0:
            return None
        return src[:k] + "\n".join("    " + x for x in missing) + "\n" + src[k:]
    return fn


ICONS_MATERIAL = {
    "IMPORTS": ("import androidx.compose.material.icons.Icons\n"
                "import androidx.compose.material.icons.filled.*\n"
                "import androidx.compose.material.icons.outlined.*"),
    "ADV_IMPORTS": ("import androidx.compose.material.icons.Icons\n"
                    "import androidx.compose.material.icons.outlined.LockOpen"),
    "FILE_OPEN": "Icons.Outlined.FileOpen",
    "SCHEDULE": "Icons.Outlined.Schedule",
    "CHECK": "Icons.Outlined.Check",
    "LOCK_OPEN": "Icons.Outlined.LockOpen",
    "STAR_FILLED": "Icons.Filled.Star",
    "STAR_OUTLINE": "Icons.Outlined.StarBorder",
}

# Newer upstream draws its icons from the Material Symbols library instead
ICONS_SYMBOLS = {
    "IMPORTS": ("import com.composables.icons.materialsymbols.MaterialSymbols\n"
                "import com.composables.icons.materialsymbols.rounded.*\n"
                "import com.composables.icons.materialsymbols.roundedfilled.*"),
    "ADV_IMPORTS": ("import com.composables.icons.materialsymbols.MaterialSymbols\n"
                    "import com.composables.icons.materialsymbols.rounded.*"),
    "FILE_OPEN": "MaterialSymbols.Rounded.File_open",
    "SCHEDULE": "MaterialSymbols.Rounded.Schedule",
    "CHECK": "MaterialSymbols.Rounded.Check",
    "LOCK_OPEN": "MaterialSymbols.Rounded.Lock_open",
    "STAR_FILLED": "MaterialSymbols.RoundedFilled.Star",
    "STAR_OUTLINE": "MaterialSymbols.Rounded.Star",
}

ICON = dict(ICONS_MATERIAL)


def icon_imports(src):
    out = src
    for line in ICON["ADV_IMPORTS"].split("\n"):
        out = ensure_import(out, line[len("import "):])
        if out is None:
            return None
    return out


FAVORITE_STRINGS = [
    ("expert_mode_favorite_added", '<string name="expert_mode_favorite_added">%s added to favorites</string>'),
    ("expert_mode_favorite_removed", '<string name="expert_mode_favorite_removed">%s removed from favorites</string>'),
    ("add_to_favorites", '<string name="add_to_favorites">Add to favorites</string>'),
    ("remove_from_favorites", '<string name="remove_from_favorites">Remove from favorites</string>'),
]

SIGNING_STRINGS = [
    ("settings_advanced_skip_signing", '<string name="settings_advanced_skip_signing">Skip APK signing</string>'),
    ("settings_advanced_skip_signing_description",
     '<string name="settings_advanced_skip_signing_description">Leave patched APKs unsigned. You can still save or send them to the selected installer, but Android normally rejects unsigned APKs</string>'),
]

APK_BUTTON_STRINGS = [
    ("floating_select_apk", '<string name="floating_select_apk">Select APK</string>'),
]

PATCH_INTERVAL_STRINGS = [
    ("settings_patch_update_interval", '<string name="settings_patch_update_interval">Patch update check</string>'),
    ("settings_patch_update_interval_value", '<string name="settings_patch_update_interval_value">Every %1$d hours</string>'),
    ("settings_patch_update_interval_description",
     '<string name="settings_patch_update_interval_description">Patches are checked when the app opens. With an interval, the check is skipped if one ran recently or while offline</string>'),
    ("settings_patch_update_interval_on_launch", '<string name="settings_patch_update_interval_on_launch">On app launch</string>'),
    ("settings_patch_update_interval_on_launch_description",
     '<string name="settings_patch_update_interval_on_launch_description">Checks every time the app starts, like stock Morphe</string>'),
]

OUR_STRINGS = [n for n, _ in FAVORITE_STRINGS + SIGNING_STRINGS + APK_BUTTON_STRINGS + PATCH_INTERVAL_STRINGS]


def app_name_edit(src):
    h = re.search(r'(<string\s+name="app_name"[^>]*>)([^<]*)(</string>)', src)
    if not h:
        return None
    if h.group(2) == FORK_APP_NAME:
        return src
    return src[:h.start(2)] + FORK_APP_NAME + src[h.end(2):]


def app_id_edit(src):
    hits = list(re.finditer(r'(\bapplicationId\s*=\s*")([^"]+)(")', src))
    if len(hits) != 1:
        return None
    h = hits[0]
    if h.group(2) == FORK_APP_ID:
        return src
    return src[:h.start(2)] + FORK_APP_ID + src[h.end(2):]


def gservices_edit(upstream):
    def fn(src):
        found = [False]

        def repl(h):
            value = h.group(2)
            if value == FORK_APP_ID or value.startswith(FORK_APP_ID + "."):
                found[0] = True
                return h.group(0)
            if value == upstream or value.startswith(upstream + "."):
                found[0] = True
                return h.group(1) + FORK_APP_ID + value[len(upstream):] + h.group(3)
            return h.group(0)

        out = re.sub(r'("package_name"\s*:\s*")([^"]+)(")', repl, src)
        return out if found[0] else None
    return fn


GMS_RX = re.compile(
    r"GoogleApiAvailability\s*\.getInstance\(\)\s*\.isGooglePlayServicesAvailable\([^)]*\)\s*==\s*ConnectionResult\.SUCCESS")


def update_source_edit(src):
    raw = "https://raw.githubusercontent.com/%s/refs/heads/updates/app-release.json" % FORK_REPO
    want = (
        ("MANAGER_REPO_URL", "https://github.com/" + FORK_REPO),
        ("MANAGER_RELEASE_JSON_URL", raw),
        ("MANAGER_PRERELEASE_JSON_URL", raw),
    )
    out = src
    for name, url in want:
        h = re.search(r'(\bconst\s+val\s+%s\s*=\s*")([^"]*)(")' % name, out)
        if not h:
            return None
        out = out[:h.start(2)] + url + out[h.end(2):]
    return out


def prerelease_filter_edit(src):
    rx = r"\(usePrereleases\s*\|\|\s*!it\.version\.contains\('-'\)\)\s*&&\s*"
    if re.search(rx, src):
        return re.sub(rx, "", src, count=1)
    return src if "versionWeight(it.version" in src else None


def no_gms_edit(src):
    if GMS_RX.search(src):
        return GMS_RX.sub("false", src)
    return src if "hasGms" in src else None


def interval_default_edit(src):
    h = re.search(r'(enumPreference\(\s*"update_check_interval"\s*,\s*UpdateCheckInterval\.)(\w+)(\s*\))', src)
    if not h:
        return None
    if h.group(2) == "HOURLY":
        return src
    return src[:h.start(2)] + "HOURLY" + src[h.end(2):]


# ---------------------------------------------------------------- PreferencesManager

def prefs_decl(name, text, anchors):
    def fn(src):
        m = mask(src)
        if re.search(r"\bval\s+%s\s*=" % name, m):
            return src
        for a in anchors:
            r = insert_after_line(src, m, a, text)
            if r is not None:
                return r
        return None
    return fn


def prefs_snapshot_field(src):
    m = mask(src)
    if re.search(r"\bval\s+favoriteUniversalPatches\s*:", m):
        return src
    text = "val favoriteUniversalPatches: Set<String>? = null,"
    r = insert_before_line(src, m, r"^[ \t]*val\s+useExpertMode\s*:\s*Boolean\?\s*=\s*null\b[^\n]*$", text)
    if r is not None:
        return r
    h = re.search(r"\bdata\s+class\s+SettingsSnapshot\s*\(\s*\n", m)
    if not h:
        return None
    return src[:h.end()] + indent_of(src, h.end()) + text + "\n" + src[h.end():]


def prefs_export(src):
    m = mask(src)
    if re.search(r"\bfavoriteUniversalPatches\s*=\s*favoriteUniversalPatches\.get\(", m):
        return src
    text = "favoriteUniversalPatches = favoriteUniversalPatches.get().takeIf { it.isNotEmpty() },"
    r = insert_before_line(src, m, r"^[ \t]*useExpertMode\s*=\s*useExpertMode\.get\(\)\s*,?[ \t]*$", text)
    if r is not None:
        return r
    h = re.search(r"\bfun\s+exportSettings\s*\([^)]*\)[^=\n{]*=\s*SettingsSnapshot\s*\(", m)
    if not h:
        return None
    return add_first_arg(src, m, h.end() - 1, text)


def prefs_restricted(src):
    m = mask(src)
    if re.search(r"\bfavoriteUniversalPatches\s*=\s*favoriteUniversalPatches\.takeIf", m):
        return src
    text = "favoriteUniversalPatches = favoriteUniversalPatches.takeIf { patching },"
    r = insert_before_line(src, m, r"^[ \t]*useExpertMode\s*=\s*useExpertMode\.takeIf\s*\{[^\n]*$", text)
    return r


def prefs_restore(src):
    m = mask(src)
    if re.search(r"\bsnapshot\.favoriteUniversalPatches\b", m):
        return src
    text = "snapshot.favoriteUniversalPatches?.let { favoriteUniversalPatches.value = it }"
    r = insert_after_line(src, m, r"^[ \t]*snapshot\.useExpertMode\?\.let\s*\{[^\n]*\}[ \t]*$", text)
    if r is not None:
        return r
    return insert_after_line(
        src, m, r"^[ \t]*(?:suspend\s+)?fun\s+importSettings\s*\([^)]*\)[^=\n{]*=\s*edit\s*\{[ \t]*$", text, extra="    ")


PREFS_EDITS = [
    ("favorites pref declaration", prefs_decl(
        "favoriteUniversalPatches",
        '\n/** Names of universal patches favorited by the user, displayed at the top of universal patch sections. */\nval favoriteUniversalPatches = stringSetPreference("favorite_universal_patches", emptySet())',
        [r"^[ \t]*val\s+useExpertMode\s*=[^\n]*$"]), False),
    ("skip signing pref declaration", prefs_decl(
        "skipApkSigning",
        '\n/** Leave patched APKs unsigned; the original META-INF signature files stay in the output. */\nval skipApkSigning = booleanPreference("skip_apk_signing", false)',
        [r"^[ \t]*val\s+stripUnusedNativeLibs\s*=[^\n]*$", r"^[ \t]*val\s+useExpertMode\s*=[^\n]*$"]), False),
    ("patch update interval prefs", prefs_decl(
        "patchUpdateIntervalHours",
        '\n/** Hours between automatic patch update checks at launch: 0 (every launch, like stock), 3, 6, 12 or 24. */\n'
        'val patchUpdateIntervalHours = intPreference("patch_update_interval_hours", 3)\n\n'
        '/** Epoch millis of the last automatic patch update check at launch. */\n'
        'val lastPatchUpdateCheck = longPreference("last_patch_update_check", 0L)',
        [r"^[ \t]*val\s+updateCheckInterval\s*=[^\n]*$", r"^[ \t]*val\s+useExpertMode\s*=[^\n]*$"]), False),
    ("SettingsSnapshot field", prefs_snapshot_field, False),
    ("export", prefs_export, False),
    ("restrictedTo", prefs_restricted, True),
    ("restore", prefs_restore, False),
]


# ---------------------------------------------------------------- SettingsViewModel

def vm_edit(src):
    m = mask(src)
    if re.search(r"\bfun\s+setSkipApkSigning\b", m):
        return src
    text = ("\n/** Leaves patched APKs unsigned, keeping the original META-INF signature files. */\n"
            "fun setSkipApkSigning(enabled: Boolean) = viewModelScope.launch {\n"
            "    prefs.skipApkSigning.update(enabled)\n}")
    f = one(find_funs(m, "setStripUnusedNativeLibs"))
    if f:
        ind = indent_of(src, f.start)
        e = line_end(src, fun_end(m, f))
        return src[:e] + "\n" + reindent(text, ind) + src[e:]
    f = one(find_funs(m, "setGitHubPat"))
    if f:
        s = doc_start(src, f.start)
        ind = indent_of(src, f.start)
        return src[:s] + reindent(text.lstrip("\n"), ind) + "\n\n" + src[s:]
    return None


# ---------------------------------------------------------------- AdvancedTabContent

SWITCH_ITEM = """SettingsSwitchItem(
    checked = skipApkSigning,
    onToggle = { settingsViewModel.setSkipApkSigning(!skipApkSigning) },
    icon = @@ICON_LOCK_OPEN@@,
    title = stringResource(R.string.settings_advanced_skip_signing),
    subtitle = stringResource(R.string.settings_advanced_skip_signing_description)
)
"""


def adv_state(src):
    m = mask(src)
    if re.search(r"\bval\s+skipApkSigning\s+by\b", m):
        return src
    text = "val skipApkSigning by prefs.skipApkSigning.getAsState()"
    r = insert_after_line(src, m, r"^[ \t]*val\s+useExpertMode\s+by\s+prefs\.useExpertMode\.getAsState\(\)[ \t]*$", text)
    if r is not None:
        return r
    return insert_after_line(src, m, r"^[ \t]*val\s+\w+\s+by\s+prefs\.\w+\.getAsState\(\)[ \t]*$", text, which=0)


def adv_switch(src):
    m = mask(src)
    if "R.string.settings_advanced_skip_signing" in m:
        return src
    # Older layouts separate rows with dividers, the grouped rows of newer ones do not
    SWITCH = SWITCH_ITEM.replace("@@ICON_LOCK_OPEN@@", ICON["LOCK_OPEN"]) + ("\nSettingsDivider()\n" if "SettingsDivider(" in m else "")
    hits = list(re.finditer(r"^[ \t]*GitHubPatSettingsItem\s*\(", m, re.M))
    if len(hits) == 1:
        s = line_start(src, hits[0].start())
        ind = indent_of(src, s)
        if s > 0:
            pls = line_start(src, s - 1)
            if src[pls:s].strip().startswith("//"):
                s = pls
        return src[:s] + reindent(SWITCH, ind) + "\n" + src[s:]
    h = re.search(
        r"if\s*\(\s*expertMode\s*\)\s*\{[ \t]*\n[ \t]*Column\([^\n]*\{[ \t]*\n([ \t]*)SettingsGroup\s*\{[ \t]*\n", m)
    if not h:
        return None
    ind = h.group(1) + "    "
    return src[:h.end()] + reindent(SWITCH, ind) + "\n" + src[h.end():]


PATCHING_SWITCH_ITEM = """FoldingVisibility(visible = useExpertMode) {
    SettingsSwitchItem(
        checked = skipApkSigning,
        onToggle = { @@VM@@.setSkipApkSigning(!skipApkSigning) },
        icon = @@ICON_LOCK_OPEN@@,
        title = stringResource(R.string.settings_advanced_skip_signing),
        subtitle = stringResource(R.string.settings_advanced_skip_signing_description)
    )
}"""


def _patching_section(src):
    m = mask(src)
    f = one(find_funs(m, "PatchingSection"))
    if not f or f.bo is None:
        return m, None
    return m, f


def patching_state(src):
    m, f = _patching_section(src)
    if not f:
        return None
    body = m[f.bo:f.bc]
    if re.search(r"\bval\s+skipApkSigning\s+by\b", body):
        return src
    h = re.search(r"^[ \t]*val\s+useExpertMode\s+by\s+([\w.]+)\.useExpertMode\.getAsState\(\)[ \t]*$", body, re.M)
    if not h:
        return None
    a = f.bo + h.start()
    e = line_end(src, f.bo + h.end())
    text = "val skipApkSigning by %s.skipApkSigning.getAsState()" % h.group(1)
    return src[:e] + "\n" + indent_of(src, a) + text + src[e:]


def patching_switch(src):
    m, f = _patching_section(src)
    if not f:
        return None
    if "R.string.settings_advanced_skip_signing" in m[f.bo:f.bc]:
        return src
    vm = re.search(r"\b(\w+)\.prefs\.useExpertMode\b", m[f.bo:f.bc])
    if not vm:
        return None
    for lp in find_calls(m, "SettingsSwitchItem", f.bo, f.bc):
        rp = match_close(m, lp)
        if rp > 0 and re.search(r"\bchecked\s*=\s*useExpertMode\b", m[lp:rp]):
            text = PATCHING_SWITCH_ITEM.replace("@@VM@@", vm.group(1)).replace("@@ICON_LOCK_OPEN@@", ICON["LOCK_OPEN"])
            e = line_end(src, rp)
            return src[:e] + "\n" + reindent(text, indent_of(src, lp)) + src[e:]
    return None


def fix_duplicate_strip_toggle(ws, adv_path):
    rx = re.compile(r"R\.string\.settings_advanced_strip_unused_libs\b")
    others = 0
    for p in ws.kt_paths():
        if p != adv_path and "/ui/" in p.as_posix():
            others += len(rx.findall(mask(ws.get(p))))
    src = ws.get(adv_path)
    m = mask(src)
    in_adv = len(rx.findall(m))
    keep = 0 if others else 1
    if in_adv <= keep:
        return
    removed = 0
    while len(rx.findall(m)) > keep:
        hits = list(rx.finditer(m))
        h = hits[-1]
        calls = find_calls(m, "SettingsSwitchItem", 0, h.start())
        if not calls:
            break
        lp = calls[-1]
        rp = match_close(m, lp)
        if not (lp < h.start() < rp):
            break
        a = line_start(src, m.rfind("SettingsSwitchItem", 0, lp + 1))
        b = line_end(src, rp)
        while a > 0:
            pls = line_start(src, a - 1)
            if src[pls:a].strip().startswith("//"):
                a = pls
            else:
                break
        src = src[:a] + src[min(b + 1, len(src)):]
        m = mask(src)
        removed += 1
    if not removed:
        return
    src = re.sub(r"([ \t]*SettingsDivider\(\)[ \t]*\n)(?:[ \t]*\n)*[ \t]*SettingsDivider\(\)[ \t]*\n", r"\1", src)
    src = re.sub(r"\n(?:[ \t]*\n)*[ \t]*SettingsDivider\(\)[ \t]*\n(?:[ \t]*\n)*([ \t]*\})", r"\n\1", src)
    src = re.sub(r"(SettingsGroup\s*\{[ \t]*\n)(?:[ \t]*\n)*[ \t]*SettingsDivider\(\)[ \t]*\n(?:[ \t]*\n)?", r"\1", src)
    unused = re.sub(r"^[ \t]*val\s+stripUnusedNativeLibs\s+by[^\n]*$", "", mask(src), flags=re.M)
    if not re.search(r"\bstripUnusedNativeLibs\b", unused):
        src = re.sub(r"^[ \t]*val\s+stripUnusedNativeLibs\s+by\s+[^\n]*\n", "", src, flags=re.M)
    ws.set(adv_path, src)
    note("removed duplicate 'Optimize for device architecture' toggle from Expert settings (kept in Patcher options)")


# ---------------------------------------------------------------- PatcherWorker

def worker_sign(src):
    m = mask(src)
    if "SignatureRestorer.restore(" in m:
        return src
    hits = list(re.finditer(r"^([ \t]*)keystoreManager\.sign\(([^\n]*)\)[ \t]*$", m, re.M))
    if len(hits) != 1 or not re.search(r"\bval\s+inputFile\b", m) or "SplitApkPreparer" not in m:
        return None
    h = hits[0]
    ind = h.group(1)
    sign_args = src[h.start(2):h.end(2)]
    parts = [x.strip() for x in split_top(sign_args)]
    if len(parts) == 1 and parts[0]:
        a_in = a_out = parts[0]
        in_place = True
    elif len(parts) >= 2:
        a_in, a_out = parts[0], parts[1]
        in_place = False
    else:
        return None

    decl = "val skipSigning = prefs.skipApkSigning.get() && prefs.useExpertMode.get()"
    hoist = None
    ls = line_start(m, h.start())
    k = ls
    while k > 0:
        pls = line_start(m, k - 1)
        line = m[pls:k]
        k = pls
        if not line.strip():
            continue
        lind = re.match(r"[ \t]*", line).group(0)
        if len(lind) < len(ind) and line.rstrip().endswith("{"):
            if "measureTime" in line:
                hoist = pls
            break
    notif = None
    region_start = h.start()
    if hoist is None and region_start > 0:
        pls = line_start(src, region_start - 1)
        prev = m[pls:region_start]
        if re.match(r"\s*updatePatcherNotification\(.*signingApkLabel.*\)\s*$", prev):
            notif = src[pls:region_start].strip()
            region_start = pls

    lines = [
        "if (skipSigning) {",
        "    if (SplitApkPreparer.isSplitArchive(inputFile)) {",
        '        args.logger.warn("Signing skipped, but the input was a split bundle: merging already cleared the original META-INF, so there is nothing to restore")',
    ]
    if not in_place:
        lines.append("        %s.copyTo(%s, overwrite = true)" % (a_in, a_out))
    lines += [
        "    } else {",
        '        args.logger.info("Signing skipped by user preference, restoring original META-INF")',
        "        withContext(Dispatchers.IO) {",
        "            SignatureRestorer.restore(inputFile, %s, %s)" % (a_in, a_out),
        "        }",
        "    }",
        "} else {",
    ]
    if notif:
        lines.append("    " + notif)
    lines += ["    keystoreManager.sign(%s)" % sign_args, "}"]
    block = reindent("\n".join(lines), ind)

    if hoist is not None:
        out = src[:region_start] + block + src[h.end():]
        hind = indent_of(src, hoist)
        return out[:hoist] + hind + decl + "\n" + out[hoist:]
    return src[:region_start] + reindent(decl, ind) + "\n" + block + src[h.end():]


def worker_autoinstall(src):
    m = mask(src)
    if re.search(r"\bautoInstallPending\s*=\s*!skipSigning\s*&&", m):
        return src
    decl = re.search(r"^([ \t]*)val\s+skipSigning\b", m, re.M)
    hits = list(re.finditer(r"^([ \t]*)autoInstallPending\s*=\s*installerManager\.autoInstallAllowed\(", m, re.M))
    if not decl or len(hits) != 1:
        return None
    h = hits[0]
    if len(h.group(1)) < len(decl.group(1)):
        return None
    pos = m.index("installerManager.autoInstallAllowed(", h.start())
    return src[:pos] + "!skipSigning && " + src[pos:]


# ---------------------------------------------------------------- select APK button / patch update interval

def add_last_arg(src, m, lp, text, marker):
    rp = match_close(m, lp)
    if rp < 0:
        return None
    if re.search(marker, m[lp:rp]):
        return src
    mm = re.match(r"[ \t]*\n([ \t]*)", src[lp + 1:rp])
    if not mm:
        return None
    ind = mm.group(1)
    end = lp + 1 + len(m[lp + 1:rp].rstrip())
    if m[end - 1] == ",":
        return src[:end] + "\n" + ind + text + "," + src[end:]
    return src[:end] + ",\n" + ind + text + src[end:]


def pill_param(src):
    m = mask(src)
    f = one(find_funs(m, "FloatingPillBar"))
    if not f:
        return None
    return add_last_param(src, m, f, "trailing: (@Composable () -> Unit)? = null,", r"\btrailing\s*:")


def pill_invoke(src):
    m = mask(src)
    f = one(find_funs(m, "FloatingPillBar"))
    if not f or f.bo is None:
        return None
    if re.search(r"\btrailing\?\.invoke\(\)", m[f.bo:f.bc]):
        return src
    rows = []
    for lp in find_calls(m, "Row", f.bo, f.bc):
        rp = match_close(m, lp)
        if rp < 0 or not re.search(r"\bhorizontalArrangement\s*=", m[lp:rp]):
            continue
        t = re.match(r"\s*\{", m[rp + 1:])
        if not t:
            continue
        bo = rp + t.end()
        bc = match_close(m, bo)
        if bc > 0:
            rows.append(bc)
    if len(rows) != 1:
        return None
    bc = rows[0]
    s = line_start(src, bc)
    if src[s:bc].strip():
        return None
    ind = indent_of(src, s) + "    "
    return src[:s] + ind + "trailing?.invoke()\n" + src[s:]


def nav_param(src):
    m = mask(src)
    f = one(find_funs(m, "MainPagerScaffold"))
    if not f:
        return None
    if re.search(r"\btrailingAction\s*:", m[f.lp:f.rp]):
        return src
    text = "trailingAction: (@Composable () -> Unit)? = null,"
    r = insert_after_line(
        src, m, r"^[ \t]*overlay\s*:\s*@Composable\s+BoxScope\.\(\)\s*->\s*Unit\s*=\s*\{\}\s*,[ \t]*$", text)
    if r is not None:
        return r
    return insert_before_line(src, m, r"^[ \t]*page\s*:\s*@Composable\b[^\n]*$", text)


def nav_pass(src):
    m = mask(src)
    calls = find_calls(m, "FloatingPillBar")
    if len(calls) != 1:
        return None
    return add_last_arg(src, m, calls[0], "trailing = trailingAction", r"\btrailing\s*=")


def home_extract(src):
    m = mask(src)
    if re.search(r"\bval\s+pickApkFromFile\b", m):
        return src
    hits = list(re.finditer(r"\bonOtherAppsClick\s*=\s*\{", m))
    calls = list(re.finditer(r"(?<![\w.])MainPagerScaffold\s*\(", m))
    if len(hits) != 1 or len(calls) != 1:
        return None
    bo = hits[0].end() - 1
    bc = match_close(m, bo)
    if bc < 0 or "return@onOtherAppsClick" in src[bo:bc]:
        return None
    body = textwrap.dedent(src[bo + 1:bc]).strip("\n")
    src = src[:bo] + "pickApkFromFile" + src[bc + 1:]
    s = line_start(src, calls[0].start())
    ind = indent_of(src, s)
    defn = "val pickApkFromFile: () -> Unit = {\n" + reindent(body, "    ") + "\n}"
    return src[:s] + reindent(defn, ind) + "\n\n" + src[s:]


def home_trailing(src):
    m = mask(src)
    calls = find_calls(m, "MainPagerScaffold")
    if len(calls) != 1:
        return None
    text = "trailingAction = { FloatingApkButton(onClick = pickApkFromFile) }"
    return add_last_arg(src, m, calls[0], text, r"\btrailingAction\s*=")


def home_flags(src):
    m = mask(src)
    if re.search(r"showOtherAppsButton\s*=\s*isLandscape\(\)", m):
        return src
    # Upstream waits for the apps to load and a third-party source before offering the button
    hits = list(re.finditer(r"\bshowOtherAppsButton\s*=\s*showOtherAppsButton\b(?!\s*&&)", m))
    if len(hits) != 1:
        return None
    h = hits[0]
    return src[:h.start()] + "showOtherAppsButton = isLandscape()" + src[h.end():]


def header_flags(src):
    m = mask(src)
    hits = []
    for lp in find_calls(m, "TitleAction"):
        rp = match_close(m, lp)
        if rp > 0 and re.search(r"\bonClick\s*=\s*onPatchFileClick\b", m[lp:rp]):
            hits.append((lp, rp))
    if len(hits) != 1:
        return None
    lp, rp = hits[0]
    start = m.rfind("TitleAction", 0, lp)
    s = line_start(src, start)
    if src[s:start].strip() or src[rp + 1:line_end(src, rp)].strip():
        return None
    if re.search(r"\bif\s*\(\s*isLandscape\(\)\s*\)\s*\{\s*$", m[:s]):
        return src
    ind = indent_of(src, start)
    block = reindent(src[s:rp + 1], "    ")
    return src[:s] + ind + "if (isLandscape()) {\n" + block + "\n" + ind + "}" + src[rp + 1:]


def app_gate(src):
    m = mask(src)
    if "PatchUpdateThrottle" in m:
        return src
    f = one(find_funs(m, "onFirstScreenCreated"))
    if not f or f.bo is None:
        return None
    hits = list(re.finditer(r"^[ \t]*patchBundleRepository\.updateCheck\(\)[ \t]*$", m[f.bo:f.bc], re.M))
    if len(hits) != 1:
        return None
    a, b = f.bo + hits[0].start(), f.bo + hits[0].end()
    ind = indent_of(src, a)
    new = (ind + "if (PatchUpdateThrottle.isDue(applicationContext, prefs)) {\n"
           + ind + "    patchBundleRepository.updateCheck()\n"
           + ind + "}")
    return src[:a] + new + src[b:]


def updates_row(src):
    m = mask(src)
    if "PatchUpdateIntervalSetting(" in m:
        return src
    text = "PatchUpdateIntervalSetting(prefs = prefs)"
    for lp in find_calls(m, "SettingsSwitchItem"):
        rp = match_close(m, lp)
        if rp > 0 and re.search(r"\bsettings_advanced_updates_allow_metered\b", m[lp:rp]):
            e = line_end(src, rp)
            ind = indent_of(src, line_start(src, lp))
            return src[:e] + "\n\n" + reindent(text, ind) + src[e:]
    return insert_before_line(src, m, r"^[ \t]*if\s*\(\s*useExpertMode\s*\)\s*\{[ \t]*$", text + "\n")


# ---------------------------------------------------------------- SettingComponents

def _single_fun(src, name):
    m = mask(src)
    f = one(find_funs(m, name))
    return m, f


def comp_param(name):
    def fn(src):
        m, f = _single_fun(src, name)
        if not f:
            return None
        return add_last_param(src, m, f, "onLongClick: (() -> Unit)? = null,", r"\bonLongClick\b")
    return fn


def comp_optin(src):
    m, f = _single_fun(src, "SurfaceCard")
    if not f:
        return None
    st = annotation_start(src, f.start)
    header = src[st:f.start]
    if "ExperimentalFoundationApi" in header:
        return src
    mo = re.search(r"@OptIn\(([^)]*)\)", header)
    if mo:
        a = st + mo.start(1)
        b = st + mo.end(1)
        return src[:a] + src[a:b].rstrip() + ", ExperimentalFoundationApi::class" + src[b:]
    return src[:st] + indent_of(src, st) + "@OptIn(ExperimentalFoundationApi::class)\n" + src[st:]


def comp_click(src):
    m, f = _single_fun(src, "SurfaceCard")
    if not f or f.bo is None:
        return None
    body_m = m[f.bo:f.bc + 1]
    if "combinedclickable" in body_m.lower():
        return src
    rx0 = re.compile(
        r"if\s*\(\s*onClick\s*!=\s*null\s*\)\s*\{(\s*)Modifier\.shapedClickable\(\s*([^,()]+?)\s*,\s*enabled\s*=\s*enabled\s*,\s*onClick\s*=\s*onClick\s*\)(\s*)\}\s*else\s*Modifier")
    h = rx0.search(body_m)
    if h:
        new = ("if (onClick != null || onLongClick != null) {" + h.group(1)
               + "Modifier.shapedCombinedClickable(" + h.group(2)
               + ", enabled = enabled, onLongClick = onLongClick, onClick = onClick ?: {})"
               + h.group(3) + "} else Modifier")
        a, b = f.bo + h.start(), f.bo + h.end()
        return src[:a] + new + src[b:]
    rx1 = re.compile(
        r"if\s*\(\s*onClick\s*!=\s*null\s*\)\s*\{(\s*)Modifier\.clickable\(\s*enabled\s*=\s*enabled\s*,\s*onClick\s*=\s*onClick\s*\)(\s*)\}\s*else\s*Modifier")
    h = rx1.search(body_m)
    if h:
        new = ("if (onClick != null || onLongClick != null) {" + h.group(1)
               + "Modifier.combinedClickable(enabled = enabled, onClick = onClick ?: {}, onLongClick = onLongClick)"
               + h.group(2) + "} else Modifier")
        a, b = f.bo + h.start(), f.bo + h.end()
        return src[:a] + new + src[b:]
    rx2 = re.compile(r"Modifier\.clickable\(\s*enabled\s*=\s*enabled\s*,\s*onClick\s*=\s*onClick\s*\)")
    h = rx2.search(body_m)
    if not h:
        return None
    note("SurfaceCard: click modifier shape changed, long-press works only when onClick is set")
    a, b = f.bo + h.start(), f.bo + h.end()
    return (src[:a] + "Modifier.combinedClickable(enabled = enabled, onClick = onClick, onLongClick = onLongClick)"
            + src[b:])


def comp_pass(src):
    m, f = _single_fun(src, "SettingsItemCard")
    if not f or f.bo is None:
        return None
    calls = find_calls(m, "SurfaceCard", f.bo, f.bc)
    if len(calls) != 1:
        return None
    return add_first_arg(src, m, calls[0], "onLongClick = onLongClick,", r"\bonLongClick\s*=")


COMPONENT_EDITS = [
    ("imports ExperimentalFoundationApi", imp("androidx.compose.foundation.ExperimentalFoundationApi"), False),
    ("imports combinedClickable", imp("androidx.compose.foundation.combinedClickable"), False),
    ("SurfaceCard onLongClick param", comp_param("SurfaceCard"), False),
    ("SurfaceCard OptIn", comp_optin, False),
    ("SurfaceCard click modifier", comp_click, False),
    ("SettingsItemCard onLongClick param", comp_param("SettingsItemCard"), False),
    ("SettingsItemCard passes onLongClick", comp_pass, False),
]


# ---------------------------------------------------------------- PatchCard

def card_params(src):
    m, f = _single_fun(src, "PatchCard")
    if not f:
        return None
    return add_last_param(
        src, m, f, "isFavorite: Boolean = false,\nonToggleFavorite: (() -> Unit)? = null,", r"\bisFavorite\b")


def card_longclick(src):
    m, f = _single_fun(src, "PatchCard")
    if not f or f.bo is None:
        return None
    calls = find_calls(m, "SettingsItemCard", f.bo, f.bc)
    if len(calls) != 1:
        return None
    return add_first_arg(
        src, m, calls[0], "onLongClick = favoriteLongClick(patch.isUniversal, onToggleFavorite),", r"\bfavoriteLongClick\b")


STAR = """if (patch.isUniversal && onToggleFavorite != null) {
    FavoriteStarButton(
        patchName = patch.displayName,
        isFavorite = isFavorite,
        onClick = onToggleFavorite
    )
}"""


def card_star(src):
    m, f = _single_fun(src, "PatchCard")
    if not f or f.bo is None:
        return None
    body = m[f.bo:f.bc + 1]
    if "FavoriteStarButton(" in body:
        return src
    h = re.search(r"\bif\s*\(\s*buildsClone\s*\)\s*\{", body)
    if h:
        ob = f.bo + h.end() - 1
        cb = match_close(m, ob)
        ind = indent_of(src, f.bo + h.start())
        return src[:cb + 1] + "\n" + reindent(STAR, ind) + src[cb + 1:]
    for lp in find_calls(m, "Text", f.bo, f.bc):
        rp = match_close(m, lp)
        if "patch.displayName" in src[lp:rp]:
            ind = indent_of(src, lp)
            note("PatchCard: badge block not found, star placed after the name text")
            return src[:rp + 1] + "\n" + reindent(STAR, ind) + src[rp + 1:]
    return None


# ---------------------------------------------------------------- ExpertModeDialog

def _dialog_step(cur):
    m = mask(cur)
    calls = [lp for lp in find_calls(m, "PatchCard") if not re.search(r"\bisFavorite\b", m[lp:match_close(m, lp)])]
    if not calls:
        return cur, False
    lp = calls[0]
    f = enclosing_fun(m, lp)
    if not f:
        return None, False
    body = m[f.bo:f.bc + 1]
    if not re.search(r"\bval\s+patchFavorites\s*=\s*rememberFavoritePatches\(\)", body):
        if re.search(r"\bpatchFavorites\b", body):
            return None, False
        mm = re.match(r"(?:[ \t]*\n)+", cur[f.bo + 1:f.bc])
        if not mm:
            return None, False
        s = f.bo + 1 + mm.end()
        ind = indent_of(cur, s)
        cur = cur[:s] + ind + "val patchFavorites = rememberFavoritePatches()\n" + cur[s:]
        return cur, True
    groups = find_calls(m, "rememberPatchGroups", f.bo, f.bc)
    if groups and "favoritesFirst" not in cur[groups[0]:match_close(m, groups[0])]:
        g = groups[0]
        gr = match_close(m, g)
        am = m[g + 1:gr]
        mm = re.search(r"(\bpatches\s*=\s*)([^,\n]+?)(\s*,)", am)
        if mm:
            expr = cur[g + 1 + mm.start(2):g + 1 + mm.end(2)]
            lam = re.search(r"\binfoOf\s*=\s*(\{[^\n]*\})", am)
            lam_txt = cur[g + 1 + lam.start(1):g + 1 + lam.end(1)] if lam else "{ (patch, _) -> patch }"
            new = ("patches = remember(" + expr + ", patchFavorites) { patchFavorites.favoritesFirst(" + expr + ") "
                   + lam_txt + " }")
            a, b = g + 1 + mm.start(1), g + 1 + mm.end(2)
            return cur[:a] + new + cur[b:], True
        note("ExpertModeDialog: rememberPatchGroups arguments changed, favorites will not float to the top")
    elif not groups:
        note("ExpertModeDialog: rememberPatchGroups call not found, favorites will not float to the top")
    rp = match_close(m, lp)
    pm = re.search(r"\bpatch\s*=\s*(\w+)", m[lp + 1:rp])
    if not pm:
        return None, False
    pv = pm.group(1)
    text = "isFavorite = patchFavorites.isFavorite(%s),\nonToggleFavorite = { patchFavorites.toggle(%s) }," % (pv, pv)
    r = add_first_arg(cur, m, lp, text, r"\bisFavorite\b")
    return r, r is not None


def make_dialog_edit(helper_pkg):
    def fn(src):
        cur = src
        if not find_calls(mask(cur), "PatchCard"):
            return None
        for _ in range(60):
            nxt, progressed = _dialog_step(cur)
            if nxt is None:
                return None
            cur = nxt
            if not progressed:
                break
        else:
            return None
        cur = ensure_import(cur, helper_pkg + ".rememberFavoritePatches")
        cur = ensure_import(cur, "androidx.compose.runtime.remember") if cur is not None else None
        return cur
    return fn


# ---------------------------------------------------------------- new files

SIGNATURE_RESTORER = r'''package @@PKG@@

import java.io.BufferedOutputStream
import java.io.File
import java.io.OutputStream
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.zip.ZipEntry
import java.util.zip.ZipFile
import java.util.zip.ZipOutputStream

object SignatureRestorer {
    private const val BLOCK_MAGIC = "APK Sig Block 42"

    fun restore(original: File, patched: File, output: File) {
        val merged = File.createTempFile("metainf-restored-", ".apk", patched.parentFile)
        try {
            mergeOriginalMetaInf(original, patched, merged)
            val block = readSigningBlock(original)
            if (block == null) {
                merged.copyTo(output, overwrite = true)
            } else {
                graftSigningBlock(block, merged, output)
            }
        } finally {
            merged.delete()
        }
    }

    private fun mergeOriginalMetaInf(original: File, patched: File, output: File) {
        val metaInf = LinkedHashMap<String, ByteArray>()
        ZipFile(original).use { src ->
            src.entries().asSequence()
                .filter { !it.isDirectory && it.name.startsWith("META-INF/") }
                .forEach { metaInf[it.name] = src.getInputStream(it).readBytes() }
        }
        ZipFile(patched).use { src ->
            ZipOutputStream(BufferedOutputStream(output.outputStream())).use { zos ->
                src.entries().asSequence()
                    .filter { !it.isDirectory && it.name !in metaInf }
                    .forEach { entry ->
                        val outEntry = ZipEntry(entry.name)
                        if (entry.method == ZipEntry.STORED) {
                            outEntry.method = ZipEntry.STORED
                            outEntry.size = entry.size
                            outEntry.crc = entry.crc
                        }
                        zos.putNextEntry(outEntry)
                        src.getInputStream(entry).use { it.copyTo(zos) }
                        zos.closeEntry()
                    }
                metaInf.forEach { (name, bytes) ->
                    zos.putNextEntry(ZipEntry(name))
                    zos.write(bytes)
                    zos.closeEntry()
                }
            }
        }
    }

    private fun readSigningBlock(apk: File): ByteArray? {
        RandomAccessFile(apk, "r").use { f ->
            val eocd = findEocd(f) ?: return null
            val cdOffset = readU32(f, eocd + 16)
            if (cdOffset < 32 || cdOffset > f.length()) return null
            f.seek(cdOffset - 16)
            val magic = ByteArray(16)
            f.readFully(magic)
            if (String(magic, Charsets.US_ASCII) != BLOCK_MAGIC) return null
            val size = readU64(f, cdOffset - 24)
            val blockStart = cdOffset - 8 - size
            if (blockStart < 0) return null
            if (readU64(f, blockStart) != size) return null
            f.seek(blockStart)
            val block = ByteArray((size + 8).toInt())
            f.readFully(block)
            return block
        }
    }

    private fun graftSigningBlock(block: ByteArray, zip: File, out: File) {
        RandomAccessFile(zip, "r").use { f ->
            val eocd = findEocd(f) ?: run {
                zip.copyTo(out, overwrite = true)
                return
            }
            val cdOffset = readU32(f, eocd + 16)
            val total = f.length()
            out.outputStream().buffered().use { o ->
                copyRange(f, o, 0, cdOffset)
                o.write(block)
                copyRange(f, o, cdOffset, eocd)
                val tail = ByteArray((total - eocd).toInt())
                f.seek(eocd)
                f.readFully(tail)
                ByteBuffer.wrap(tail).order(ByteOrder.LITTLE_ENDIAN)
                    .putInt(16, (cdOffset + block.size).toInt())
                o.write(tail)
            }
        }
    }

    private fun findEocd(f: RandomAccessFile): Long? {
        val len = f.length()
        val start = maxOf(0L, len - 22 - 65535)
        val buf = ByteArray((len - start).toInt())
        f.seek(start)
        f.readFully(buf)
        for (i in buf.size - 22 downTo 0) {
            if (buf[i].toInt() == 0x50 &&
                buf[i + 1].toInt() == 0x4b &&
                buf[i + 2].toInt() == 0x05 &&
                buf[i + 3].toInt() == 0x06
            ) {
                return start + i
            }
        }
        return null
    }

    private fun readU32(f: RandomAccessFile, pos: Long): Long {
        val b = ByteArray(4)
        f.seek(pos)
        f.readFully(b)
        var v = 0L
        for (i in 3 downTo 0) {
            v = (v shl 8) or (b[i].toInt() and 0xff).toLong()
        }
        return v
    }

    private fun readU64(f: RandomAccessFile, pos: Long): Long {
        val b = ByteArray(8)
        f.seek(pos)
        f.readFully(b)
        var v = 0L
        for (i in 7 downTo 0) {
            v = (v shl 8) or (b[i].toInt() and 0xff).toLong()
        }
        return v
    }

    private fun copyRange(f: RandomAccessFile, out: OutputStream, from: Long, to: Long) {
        f.seek(from)
        var remaining = to - from
        val buf = ByteArray(64 * 1024)
        while (remaining > 0) {
            val read = f.read(buf, 0, minOf(buf.size.toLong(), remaining).toInt())
            if (read < 0) break
            out.write(buf, 0, read)
            remaining -= read
        }
    }
}
'''

FAVORITE_PATCHES = r'''package @@PKG@@

import androidx.compose.foundation.layout.size
@@ICON_IMPORTS@@
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import @@NS@@.R
import @@PREFS_PKG@@.PreferencesManager
import @@PATCHINFO_PKG@@.PatchInfo
@@TOAST_IMPORT@@
import kotlinx.coroutines.launch
import org.koin.compose.koinInject

@Stable
internal class FavoritePatches(
    val names: Set<String>,
    private val onToggle: (PatchInfo) -> Unit
) {
    fun isFavorite(patch: PatchInfo): Boolean =
        patch.isUniversal && (patch.name in names || patch.displayName in names)

    fun toggle(patch: PatchInfo) = onToggle(patch)

    fun <T> favoritesFirst(items: List<T>, infoOf: (T) -> PatchInfo): List<T> =
        if (names.isEmpty()) items else items.sortedByDescending { isFavorite(infoOf(it)) }
}

@Composable
internal fun rememberFavoritePatches(): FavoritePatches {
    val prefs: PreferencesManager = koinInject()
    val names by prefs.favoriteUniversalPatches.getAsState()
    @@TOAST_DECL@@
    val scope = rememberCoroutineScope()
    return remember(names) {
        FavoritePatches(names) { patch ->
            val wasFavorite = patch.name in names || patch.displayName in names
            val updated = if (wasFavorite) names - patch.name - patch.displayName else names + patch.name
            scope.launch { prefs.favoriteUniversalPatches.update(updated) }
            @@TOAST_CALL@@
        }
    }
}

@Composable
internal fun favoriteLongClick(enabled: Boolean, onToggle: (() -> Unit)?): (() -> Unit)? {
    val haptic = LocalHapticFeedback.current
    if (!enabled || onToggle == null) return null
    return {
        haptic.performHapticFeedback(HapticFeedbackType.LongPress)
        onToggle()
    }
}

@Composable
internal fun FavoriteStarButton(
    patchName: String,
    isFavorite: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier
) {
    val actionLabel = stringResource(
        if (isFavorite) R.string.remove_from_favorites else R.string.add_to_favorites
    )
    val description = "$patchName, $actionLabel"
    IconButton(
        onClick = onClick,
        modifier = modifier
            .size(28.dp)
            .semantics { contentDescription = description }
    ) {
        Icon(
            imageVector = if (isFavorite) @@ICON_STAR_FILLED@@ else @@ICON_STAR_OUTLINE@@,
            contentDescription = null,
            modifier = Modifier.size(18.dp),
            tint = if (isFavorite) Color(0xFFFFB300) else MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.6f)
        )
    }
}
'''


# ---------------------------------------------------------------- workspace

FLOATING_APK_BUTTON = r'''package @@PKG@@

import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
@@ICON_IMPORTS@@
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.ripple
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import @@NS@@.R
import @@SHARED_PKG@@.*

/**
 * Button beside the bottom pill that opens the select-APK flow. Built as one slot of that pill,
 * with its height, rounding, icon size and label, so the two read as one control. Always shown:
 * it does not wait for the apps to load.
 */
@Composable
internal fun FloatingApkButton(
    onClick: () -> Unit,
    modifier: Modifier = Modifier
) {
    val label = stringResource(R.string.floating_select_apk)
    val interactionSource = remember { MutableInteractionSource() }
    val shape = rememberPressShape(interactionSource, CircleShape)
    val tint = MaterialTheme.colorScheme.primary

    Row(verticalAlignment = Alignment.CenterVertically) {
        Spacer(modifier = Modifier.width(Defaults.SpaceSmall))
        Surface(
            modifier = modifier
                .height(FloatingApkButtonHeight)
                .widthIn(min = FloatingApkButtonHeight, max = FloatingApkButtonMaxWidth)
                .glassFill(GlassButtonDefaults.containerColor(), CircleShape),
            shape = CircleShape,
            color = Color.Transparent,
            border = CardBorder.of(GlassButtonDefaults.borderColor())
        ) {
            Column(
                modifier = Modifier
                    .padding(FloatingApkButtonInset)
                    .height(FloatingApkButtonHeight - FloatingApkButtonInset * 2)
                    .pressScale(interactionSource, label = "floating_apk_press_scale")
                    .clip(shape)
                    .clickable(
                        interactionSource = interactionSource,
                        indication = ripple(),
                        role = Role.Button,
                        onClick = rememberHapticClick(onClick)
                    ),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center
            ) {
                Icon(
                    imageVector = @@ICON_FILE_OPEN@@,
                    contentDescription = null,
                    tint = tint,
                    modifier = Modifier.size(22.dp)
                )
                Text(
                    text = label,
                    style = MaterialTheme.typography.labelSmall,
                    color = tint,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.padding(horizontal = 10.dp)
                )
            }
        }
    }
}

private val FloatingApkButtonHeight = 64.dp
private val FloatingApkButtonInset = 6.dp
private val FloatingApkButtonMaxWidth = 128.dp
'''

PATCH_UPDATE_THROTTLE = r'''package @@PKG@@

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities

/** Limits the automatic patch update check at launch to once per chosen number of hours. */
object PatchUpdateThrottle {
    /** Stock behavior: the check runs at every launch. */
    const val ON_LAUNCH = 0
    const val DEFAULT_HOURS = 3
    val OPTIONS_HOURS = listOf(ON_LAUNCH, 3, 6, 12, 24)

    /**
     * True when a check should run now. Stamps the time when it returns true, so the next launch
     * inside the interval skips the check. Offline launches skip without stamping.
     * [ON_LAUNCH] always returns true and leaves the rest to the stock check.
     */
    suspend fun isDue(context: Context, prefs: PreferencesManager): Boolean {
        val hours = prefs.patchUpdateIntervalHours.get().takeIf { it in OPTIONS_HOURS } ?: DEFAULT_HOURS
        if (hours == ON_LAUNCH) return true
        if (!isOnline(context)) return false
        val last = prefs.lastPatchUpdateCheck.get()
        val now = System.currentTimeMillis()
        // A stamp in the future means the clock moved back, so the check is due
        if (last in 1..now && now - last < hours * 3_600_000L) return false
        prefs.lastPatchUpdateCheck.update(now)
        return true
    }

    private fun isOnline(context: Context): Boolean = runCatching {
        val cm = context.getSystemService(ConnectivityManager::class.java) ?: return@runCatching true
        val network = cm.activeNetwork ?: return@runCatching false
        cm.getNetworkCapabilities(network)?.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) == true
    }.getOrDefault(true)
}
'''

PATCH_UPDATE_INTERVAL_SETTING = r'''package @@PKG@@

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
@@ICON_IMPORTS@@
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.res.stringResource
import @@NS@@.R
import @@PREFS_PKG@@.PatchUpdateThrottle
import @@PREFS_PKG@@.PreferencesManager
import @@SHARED_PKG@@.*
import kotlinx.coroutines.launch

@Composable
private fun intervalLabel(hours: Int): String =
    if (hours == PatchUpdateThrottle.ON_LAUNCH) stringResource(R.string.settings_patch_update_interval_on_launch)
    else stringResource(R.string.settings_patch_update_interval_value, hours)

/** Row that opens a dialog picking how often the launch-time patch update check may run. */
@Composable
internal fun PatchUpdateIntervalSetting(prefs: PreferencesManager) {
    val hours by prefs.patchUpdateIntervalHours.getAsState()
    val scope = rememberCoroutineScope()
    var showDialog by remember { mutableStateOf(false) }

    SettingsItem(
        onClick = { showDialog = true },
        icon = @@ICON_SCHEDULE@@,
        title = stringResource(R.string.settings_patch_update_interval),
        subtitle = intervalLabel(hours)
    )

    DialogHost(visible = showDialog) {
        AppDialog(
            onDismissRequest = { showDialog = false },
            title = stringResource(R.string.settings_patch_update_interval),
            description = stringResource(R.string.settings_patch_update_interval_description),
            dismissOnClickOutside = true
        ) {
            Column(verticalArrangement = Arrangement.spacedBy(Defaults.SpaceSmall)) {
                PatchUpdateThrottle.OPTIONS_HOURS.forEach { option ->
                    RadioSelectionCard(
                        selected = option == hours,
                        onSelect = {
                            showDialog = false
                            scope.launch { prefs.patchUpdateIntervalHours.update(option) }
                        },
                        title = intervalLabel(option),
                        description = if (option == PatchUpdateThrottle.ON_LAUNCH) {
                            stringResource(R.string.settings_patch_update_interval_on_launch_description)
                        } else null
                    )
                }
            }
        }
    }
}
'''


def _replace_in_fun(src, name, steps):
    for rx, repl in steps:
        m = mask(src)
        f = one(find_funs(m, name))
        if not f or f.bo is None:
            return None
        hits = list(re.finditer(rx, m[f.bo:f.bc], re.M))
        if len(hits) != 1:
            return None
        h = hits[0]
        a, b = f.bo + h.start(), f.bo + h.end()
        src = src[:a] + h.expand(repl) + src[b:]
    return src


def hbar_param(src):
    m = mask(src)
    f = one(find_funs(m, "HeaderBar"))
    if not f:
        return None
    return add_last_param(src, m, f, "searchFirst: Boolean = false,", r"\bsearchFirst\s*:")


def hbar_layout(src):
    m = mask(src)
    f = one(find_funs(m, "HeaderBar"))
    if not f or f.bo is None:
        return None
    if re.search(r"\bval\s+lead\b", m[f.bo:f.bc]):
        return src
    steps = [
        (r"^([ \t]*)val\s+shift\s*=\s*-\(p\s*\*\s*\(width\s*-\s*toggleWidth\)\)\.roundToInt\(\)[ \t]*$",
         "\\1// Toggle ahead of the actions: they stand at its end and leave the other way as the field opens\n"
         "\\1val tail = if (searchFirst && actionsP != null && toggleP != null) actionsP.width + controlGap else 0\n"
         "\\1val lead = ((1f - p) * tail).roundToInt()\n"
         "\\1val shift = -(p * (width - toggleWidth - tail)).roundToInt()"),
        (r"^([ \t]*)actionsP\?\.placeRelativeWithLayer\(\s*width\s*-\s*controlsWidth\s*\+\s*shift\s*,"
         r"\s*centered\(actionsP\.height\)\s*\)\s*\{\s*alpha\s*=\s*fade\s*\}[ \t]*$",
         "\\1actionsP?.let { a ->\n"
         "\\1    val x = if (tail > 0) width - a.width + (p * tail).roundToInt() else width - controlsWidth + shift\n"
         "\\1    a.placeRelativeWithLayer(x, centered(a.height)) { alpha = fade }\n"
         "\\1}"),
        (r"^([ \t]*)fieldP\?\.placeRelative\(\s*0\s*,\s*centered\(fieldP\.height\)\s*\)[ \t]*$",
         "\\1fieldP?.placeRelative(-lead, centered(fieldP.height))"),
        (r"^([ \t]*)toggleP\?\.placeRelative\(\s*width\s*-\s*toggleWidth\s*,\s*centered\(toggleP\.height\)\s*\)[ \t]*$",
         "\\1toggleP?.placeRelative(width - toggleWidth - lead, centered(toggleP.height))"),
    ]
    return _replace_in_fun(src, "HeaderBar", steps)


def phead_param(src):
    m = mask(src)
    f = one(find_funs(m, "PageHeader"))
    if not f:
        return None
    return add_last_param(src, m, f, "searchFirst: Boolean = false,", r"\bsearchFirst\s*:")


def phead_pass(src):
    m = mask(src)
    f = one(find_funs(m, "PageHeader"))
    if not f or f.bo is None:
        return None
    calls = find_calls(m, "HeaderBar", f.bo, f.bc)
    if len(calls) != 1:
        return None
    return add_last_arg(src, m, calls[0], "searchFirst = searchFirst", r"\bsearchFirst\s*=")


def sources_search_first(src):
    m = mask(src)
    calls = [lp for lp in find_calls(m, "PageHeader")
             if re.search(r"\bsearch\s*=\s*search\b", m[lp:match_close(m, lp)])]
    if len(calls) != 1:
        return None
    return add_last_arg(src, m, calls[0], "searchFirst = true", r"\bsearchFirst\s*=")


def bundles_app_first(src):
    m = mask(src)
    h = re.search(r"\bfun\s+List<[^\n]*>\.listedWith\s*\(", m)
    if not h:
        return None
    lo = h.end() - 1
    nxt = m.find("\n    /**", lo)
    hi = len(m) if nxt < 0 else nxt
    region = m[lo:hi]
    if re.search(r"\bbundle\.incompatible\.isNotEmpty\(\)", region):
        return src
    hits = list(re.finditer(
        r"compareByDescending<[^\n]*>\s*\{\s*\(bundle,\s*_\)\s*->\s*bundle\.targetsVersion\s*\}", region))
    if len(hits) != 1:
        return None
    a, b = lo + hits[0].start(), lo + hits[0].end()
    ind = indent_of(src, a)
    old = src[a:b]
    new = (
        "// A bundle with a patch of its own for the app, not only universal ones, comes ahead of the rest\n"
        + ind + old.replace("bundle.targetsVersion", "bundle.compatible.isNotEmpty() || bundle.incompatible.isNotEmpty()") + "\n"
        + ind + "    .thenByDescending { (bundle, _) -> bundle.targetsVersion }"
    )
    return src[:a] + new + src[b:]


def host_quiet_universal(src):
    if "checkUniversalPatchDefaults" in src:
        return src
    m = mask(src)
    f = one(find_funs(m, "printer"))
    if not f or f.eq is None:
        return None
    lp = m.find("(", f.eq)
    if lp < 0:
        return None
    rp = match_close(m, lp)
    if rp < 0:
        return None
    old = src[f.start:rp + 1]
    if "LineOutputStream" not in old or not re.search(r'\[STDIO\]:\s*\$line', old):
        return None
    block = textwrap.dedent("""\
        fun printer(): PrintStream {
            // The loader's notice that universal patches lack default false is a header, the message and
            // the bundles and patches indented under it. They are loaded with default false either way
            var skipping = false
            return PrintStream(
                LineOutputStream { line ->
                    val notice = "checkUniversalPatchDefaults" in line ||
                            line.startsWith("WARNING: Universal patches must be declared with default false")
                    skipping = notice || (skipping && line.isNotEmpty() && line[0].isWhitespace())
                    if (!skipping) logger.warn("[STDIO]: $line")
                },
                true,
                Charsets.UTF_8.name()
            )
        }""")
    ind = indent_of(src, f.start)
    new = reindent(block, ind)[len(ind):]
    return src[:f.start] + new + src[rp + 1:]


class Workspace:
    def __init__(self, root):
        self.root = root
        self.main = root / "app" / "src" / "main"
        self.orig = {}
        self.cur = {}
        self.fmt = {}
        for p in sorted(self.main.rglob("*.kt")):
            self.load(p)

    def load(self, p):
        raw = p.read_bytes().decode("utf-8")
        bom = raw.startswith(BOM)
        if bom:
            raw = raw[1:]
        crlf = "\r\n" in raw
        text = raw.replace("\r\n", "\n")
        self.orig[p] = text
        self.cur[p] = text
        self.fmt[p] = (bom, crlf)

    def ensure(self, p):
        if p not in self.cur and p.is_file():
            self.load(p)
        return p in self.cur

    def get(self, p):
        return self.cur[p]

    def set(self, p, text):
        self.cur[p] = text

    def kt_paths(self):
        return [p for p in self.cur if p.suffix == ".kt"]

    def add_new(self, p, text):
        self.orig.setdefault(p, None)
        self.cur[p] = text
        self.fmt.setdefault(p, (False, False))

    def find(self, rx, hint=None, exclude=(), multi=False):
        flags = re.M
        if hint is not None and not multi:
            h = self.root / hint
            if self.ensure(h) and re.search(rx, mask(self.cur[h]), flags):
                return [h]
        hits = [p for p in self.kt_paths()
                if p not in exclude and self.orig.get(p) is not None and re.search(rx, mask(self.orig[p]), flags)]
        return hits

    def write(self, p):
        text = self.cur[p]
        bom, crlf = self.fmt[p]
        if crlf:
            text = text.replace("\n", "\r\n")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(((BOM if bom else "") + text).encode("utf-8"))


P = "app/src/main/java/app/morphe/manager/"


def find_outside_app(ws, rx):
    skip = {"build", "node_modules", "test", "androidTest", ".git", ".gradle"}
    for p in sorted(ws.root.rglob("*.kt")):
        rel = p.relative_to(ws.root).parts
        if skip.intersection(rel) or p.is_relative_to(ws.main):
            continue
        text = p.read_bytes().decode("utf-8").lstrip(BOM).replace("\r\n", "\n")
        if re.search(rx, mask(text), re.M):
            return p, text
    return None


def build_plan(ws):
    problems = []
    plan = []

    def locate(label, rx, hint, multi=False, exclude=()):
        hits = ws.find(rx, hint, exclude, multi)
        if not hits:
            problems.append("cannot locate %s (%s)" % (label, rx))
            return [] if multi else None
        if multi:
            return hits
        if len(hits) != 1:
            problems.append("ambiguous %s: %s" % (label, ", ".join(str(h.relative_to(ws.root)) for h in hits)))
            return None
        return hits[0]

    prefs = locate("PreferencesManager", r"^class\s+PreferencesManager\b", P + "domain/manager/PreferencesManager.kt")
    vm = locate("SettingsViewModel", r"^class\s+SettingsViewModel\b", P + "ui/viewmodel/SettingsViewModel.kt")
    # Newest layout: the Patching section of the settings page, whose Expert mode switch the skip row follows
    adv = ws.find(r"\bfun\s+PatchingSection\s*\(", P + "ui/screen/settings/SettingsPage.kt")
    skip_edits = [
        ("skip signing state", patching_state, False),
        ("skip signing switch", patching_switch, False),
        ("icon imports", icon_imports, False),
    ]
    if len(adv) != 1:
        adv = ws.find(r"\bfun\s+AdvancedTabContent\s*\(", P + "ui/screen/settings/AdvancedTabContent.kt")
        skip_edits = [
            ("skip signing state", adv_state, False),
            ("skip signing switch", adv_switch, False),
            ("icon imports", icon_imports, False),
        ]
    if len(adv) != 1:
        # Older layouts split the tab into sections, the expert-only rows living with the GitHub token
        adv = ws.find(r"^[ \t]*GitHubPatSettingsItem\s*\(", P + "ui/screen/settings/advanced/UpdatesItems.kt")
    if len(adv) == 1:
        adv = adv[0]
    else:
        problems.append("cannot locate the expert settings rows (PatchingSection / AdvancedTabContent / GitHubPatSettingsItem call)")
        adv = None
    updates = locate("UpdatesItems", r"\bfun\s+UpdatesItems\s*\(", P + "ui/screen/settings/advanced/UpdatesItems.kt")
    worker = locate("PatcherWorker", r"^class\s+PatcherWorker\b", P + "patcher/worker/PatcherWorker.kt")
    card = locate("PatchCard", r"\bfun\s+PatchCard\s*\(", P + "ui/screen/home/ExpertPatchCard.kt")
    comps = locate("SurfaceCard", r"\bfun\s+SurfaceCard\s*\(", P + "ui/screen/shared/SettingComponents.kt")
    dialogs = locate("PatchCard call sites", r"(?<![\w.])PatchCard\s*\(", P + "ui/screen/home/ExpertModeDialog.kt",
                     multi=True, exclude=(card,) if card else ())
    stripper = ws.find(r"\bobject\s+NativeLibs\b", P + "patcher/util/NativeLibs.kt")
    if not stripper:
        stripper = ws.find(r"\bobject\s+NativeLibStripper\b", P + "patcher/util/NativeLibStripper.kt")
    stripper_ext = None
    if not stripper:
        stripper_ext = find_outside_app(ws, r"\bobject\s+(?:NativeLibs|NativeLibStripper)\b")
        if stripper_ext:
            stripper = [stripper_ext[0]]
    pill = locate("FloatingPillBar", r"\bfun\s+FloatingPillBar\s*\(", P + "ui/screen/shared/FloatingPillBar.kt")
    nav = locate("MainPagerScaffold", r"\bfun\s+MainPagerScaffold\s*\(", P + "ui/screen/shared/MainNavigation.kt")
    home = locate("HomeScreen", r"\bfun\s+HomeScreen\s*\(", P + "ui/screen/HomeScreen.kt")
    app = locate("ManagerApplication", r"^class\s+ManagerApplication\b", P + "ManagerApplication.kt")
    hbar = locate("HeaderBar", r"\bfun\s+HeaderBar\s*\(", P + "ui/screen/shared/HeaderBar.kt")
    phead = locate("PageHeader", r"\bfun\s+PageHeader\s*\(", P + "ui/screen/shared/PageScaffold.kt")
    host_ext = None
    host = ws.find(r"\bclass\s+HostEvents\b")
    if len(host) == 1:
        host_ext = (host[0], None)
    else:
        found = find_outside_app(ws, r"\bclass\s+HostEvents\b")
        if found:
            host_ext = found
        else:
            problems.append("cannot locate HostEvents (patcher log STDIO printer)")
    selext = locate("PatchSelectionUtils", r"\bobject\s+PatchSelectionUtils\b", P + "util/PatchSelectionExtensions.kt")
    srcpage = locate("SourcesPageContent", r"\bfun\s+SourcesPageContent\s*\(", P + "ui/screen/sources/SourcesPage.kt")
    patchinfo = ws.find(r"\bclass\s+PatchInfo\b", P + "patcher/patch/PatchInfo.kt")
    toast = ws.find(r"\bfun\s+Context\.toast\s*\(", P + "util/Util.kt")
    # Newer upstream shows its messages through UserMessages instead of a toast helper
    usermsgs = ws.find(r"\bclass\s+UserMessages\b", P + "ui/screen/shared/UserMessages.kt")

    consts = api = postprompts = None
    if FORK_REPO:
        consts = locate("Constants", r"\bconst\s+val\s+MANAGER_RELEASE_JSON_URL\b", P + "util/Constants.kt")
        api = locate("MorpheAPI.getAppUpdate", r"\bsuspend\s+fun\s+getAppUpdate\s*\(", P + "network/api/MorpheAPI.kt")
        postprompts = locate("PostPatchPrompts", r"\bval\s+hasGms\s*=", P + "ui/model/PostPatchPrompts.kt")

    strings = ws.main / "res" / "values" / "strings.xml"
    gradle = ws.root / "app" / "build.gradle.kts"
    gservices = ws.root / "app" / "google-services.json"
    for p in (strings, gradle):
        if not p.is_file():
            problems.append("missing " + str(p.relative_to(ws.root)))
    if problems:
        return None, problems
    ws.load(strings)
    ws.load(gradle)
    has_gservices = gservices.is_file()
    if has_gservices:
        ws.load(gservices)

    ns = re.search(r'\bnamespace\s*=\s*"([^"]+)"', ws.get(gradle))
    idm = re.search(r'\bapplicationId\s*=\s*"([^"]+)"', ws.get(gradle))
    upstream = idm.group(1) if idm and idm.group(1) != FORK_APP_ID else DEFAULT_UPSTREAM_APP_ID
    if not ns:
        return None, ["namespace not found in app/build.gradle.kts"]
    if not (stripper and patchinfo and (usermsgs or toast)):
        missing = [n for n, v in (("NativeLibs", stripper), ("PatchInfo", patchinfo),
                                  ("UserMessages / Context.toast", usermsgs or toast)) if not v]
        return None, ["cannot locate " + ", ".join(missing)]

    symbols = ws.find(r"^import\s+com\.composables\.icons\.materialsymbols\.MaterialSymbols\s*$",
                      P + "ui/screen/home/HomeAppsToolbar.kt")
    ICON.clear()
    ICON.update(ICONS_SYMBOLS if symbols else ICONS_MATERIAL)

    if stripper_ext:
        util_pkg = package_of(stripper_ext[1])
        util_dir = ws.main / "java" / pathlib.Path(*util_pkg.split("."))
    else:
        util_dir = stripper[0].parent
        util_pkg = package_of(ws.get(stripper[0]))
    helper_dir = card.parent
    helper_pkg = package_of(ws.get(card))
    fmt = {
        "PKG": None,
        "NS": ns.group(1),
        "PREFS_PKG": package_of(ws.get(prefs)),
        "PATCHINFO_PKG": package_of(ws.get(patchinfo[0])),
        "TOAST_IMPORT": "import %s.UserMessages" % package_of(ws.get(usermsgs[0])) if usermsgs
        else "import %s.toast" % package_of(ws.get(toast[0])),
        "TOAST_DECL": "val messages: UserMessages = koinInject()" if usermsgs else "val context = LocalContext.current",
        "TOAST_CALL": (
            "messages.show(\n"
            "                if (wasFavorite) R.string.expert_mode_favorite_removed else R.string.expert_mode_favorite_added,\n"
            "                patch.displayName\n"
            "            )") if usermsgs else (
            "context.toast(\n"
            "                context.getString(\n"
            "                    if (wasFavorite) R.string.expert_mode_favorite_removed else R.string.expert_mode_favorite_added,\n"
            "                    patch.displayName\n"
            "                )\n"
            "            )"),
        "SHARED_PKG": package_of(ws.get(pill)),
    }
    fmt.update({"ICON_" + k: v for k, v in ICON.items()})

    def render(tpl, pkg):
        out = tpl.replace("@@PKG@@", pkg)
        for k, v in fmt.items():
            if v:
                out = out.replace("@@%s@@" % k, v)
        return out

    plan.append(("res/values/strings.xml", strings, [
        ("favorite strings", strings_edit(FAVORITE_STRINGS, "expert_mode_universal_patches"), False),
        ("skip signing strings", strings_edit(SIGNING_STRINGS, "settings_advanced_strip_unused_libs_description"), False),
        ("app name", app_name_edit, False),
        ("select APK button string", strings_edit(APK_BUTTON_STRINGS, "home_other_apps_action"), False),
        ("patch update interval strings", strings_edit(PATCH_INTERVAL_STRINGS, "settings_advanced_update_interval"), False),
    ]))
    plan.append(("app/build.gradle.kts", gradle, [("applicationId", app_id_edit, False)]))
    if has_gservices:
        plan.append(("app/google-services.json", gservices, [("package_name", gservices_edit(upstream), False)]))
    plan.append((str(prefs.relative_to(ws.root)), prefs,
                 PREFS_EDITS + ([("update interval default", interval_default_edit, False)] if FORK_REPO else [])))
    plan.append((str(vm.relative_to(ws.root)), vm,
                 [("setSkipApkSigning", vm_edit, False)]
                 + ([("update worker without GMS gate", no_gms_edit, False)] if FORK_REPO else [])))
    plan.append((str(adv.relative_to(ws.root)), adv, skip_edits))
    plan.append((str(worker.relative_to(ws.root)), worker, [
        ("import SignatureRestorer", imp(util_pkg + ".SignatureRestorer"), False),
        ("signing branch", worker_sign, False),
        ("auto-install guard", worker_autoinstall, False),
    ]))
    plan.append((str(pill.relative_to(ws.root)), pill, [
        ("pill trailing param", pill_param, False),
        ("pill trailing slot", pill_invoke, False),
    ]))
    plan.append((str(nav.relative_to(ws.root)), nav, [
        ("scaffold trailingAction param", nav_param, False),
        ("pill trailing pass-through", nav_pass, False),
    ]))
    land = ws.find(r"\bfun\s+isLandscape\s*\(", P + "ui/screen/shared/WindowSize.kt")
    land_pkg = package_of(ws.get(land[0])) if land else package_of(ws.get(pill))
    # Older upstream gates the toolbar button in HomeScreen with a flag, newer keeps it in HomeHeader
    legacy_flag = re.search(r"\bshowOtherAppsButton\s*=\s*(?:showOtherAppsButton|isLandscape\(\))", mask(ws.get(home)))
    header = ws.find(r"\bfun\s+HomeHeader\s*\(", P + "ui/screen/home/HomeHeader.kt")
    if not legacy_flag and len(header) != 1:
        return None, ["cannot locate the toolbar button (showOtherAppsButton flag / HomeHeader)"]
    home_edits = [
        ("pickApkFromFile extract", home_extract, False),
        ("trailing APK button", home_trailing, False),
    ]
    if legacy_flag:
        home_edits += [
            ("toolbar button landscape only", home_flags, False),
            ("import isLandscape", imp(land_pkg + ".isLandscape"), False),
        ]
    home_edits.append(("import FloatingApkButton", imp(helper_pkg + ".FloatingApkButton"), False))
    plan.append((str(home.relative_to(ws.root)), home, home_edits))
    if not legacy_flag:
        plan.append((str(header[0].relative_to(ws.root)), header[0], [
            ("toolbar button landscape only", header_flags, False),
            ("import isLandscape", imp(land_pkg + ".isLandscape"), False),
        ]))
    plan.append((str(app.relative_to(ws.root)), app, [
        ("patch update throttle", app_gate, False),
        ("import PatchUpdateThrottle", imp(package_of(ws.get(prefs)) + ".PatchUpdateThrottle"), False),
    ] + ([("update worker without GMS gate", no_gms_edit, False)] if FORK_REPO else [])))
    if FORK_REPO:
        plan.append((str(consts.relative_to(ws.root)), consts, [("fork update source", update_source_edit, False)]))
        plan.append((str(api.relative_to(ws.root)), api, [("stable channel accepts dev versions", prerelease_filter_edit, False)]))
        plan.append((str(postprompts.relative_to(ws.root)), postprompts, [("update worker without GMS gate", no_gms_edit, False)]))
    plan.append((str(updates.relative_to(ws.root)), updates, [("patch update interval row", updates_row, False)]))
    plan.append((str(hbar.relative_to(ws.root)), hbar, [
        ("header searchFirst param", hbar_param, False),
        ("header search toggle ahead of actions", hbar_layout, False),
    ]))
    plan.append((str(phead.relative_to(ws.root)), phead, [
        ("page header searchFirst param", phead_param, False),
        ("page header searchFirst pass-through", phead_pass, False),
    ]))
    plan.append((str(srcpage.relative_to(ws.root)), srcpage, [("sources header search first", sources_search_first, False)]))
    host_path = host_ext[0]
    ws.ensure(host_path)
    plan.append((str(host_path.relative_to(ws.root)), host_path, [("quiet universal default notice", host_quiet_universal, False)]))
    plan.append((str(selext.relative_to(ws.root)), selext, [("expert bundles with app patches first", bundles_app_first, False)]))
    plan.append((str(comps.relative_to(ws.root)), comps, COMPONENT_EDITS))
    plan.append((str(card.relative_to(ws.root)), card, [
        ("PatchCard params", card_params, False),
        ("PatchCard long-press", card_longclick, False),
        ("PatchCard star", card_star, False),
    ]))
    for d in dialogs:
        plan.append((str(d.relative_to(ws.root)), d, [("favorites wiring", make_dialog_edit(helper_pkg), False)]))

    new_files = [
        (util_dir / "SignatureRestorer.kt", render(SIGNATURE_RESTORER, util_pkg)),
        (helper_dir / "FavoritePatches.kt", render(FAVORITE_PATCHES, helper_pkg)),
        (helper_dir / "FloatingApkButton.kt", render(FLOATING_APK_BUTTON, helper_pkg)),
        (prefs.parent / "PatchUpdateThrottle.kt", render(PATCH_UPDATE_THROTTLE, package_of(ws.get(prefs)))),
        (updates.parent / "PatchUpdateIntervalSetting.kt", render(PATCH_UPDATE_INTERVAL_SETTING, package_of(ws.get(updates)))),
    ]
    needles = [
        (r"\bval\s+skipApkSigning\s*=", prefs, "skipApkSigning preference"),
        (r"\bval\s+favoriteUniversalPatches\s*=", prefs, "favoriteUniversalPatches preference"),
        (r"\bfun\s+setSkipApkSigning\b", vm, "setSkipApkSigning"),
        (r"\bval\s+patchUpdateIntervalHours\s*=", prefs, "patchUpdateIntervalHours preference"),
        (r"\bval\s+lastPatchUpdateCheck\s*=", prefs, "lastPatchUpdateCheck preference"),
    ]
    return (plan, new_files, adv, needles), []


def verify(ws, changed, strings_path, needles):
    errors = []
    for p in changed:
        if p.suffix == ".kt" and not balanced(mask(ws.get(p))):
            errors.append("%s: unbalanced brackets after edit" % p.relative_to(ws.root))
    strings_text = ws.get(strings_path)
    for p in changed:
        if p.suffix != ".kt":
            continue
        for name in set(re.findall(r"R\.string\.(\w+)", mask(ws.get(p)))):
            if name in OUR_STRINGS and 'name="%s"' % name not in strings_text:
                errors.append("%s: R.string.%s not defined" % (p.relative_to(ws.root), name))
    for rx, path, what in needles:
        if not re.search(rx, mask(ws.get(path))):
            errors.append("missing " + what)
    return errors


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?", default=".", help="morphe-manager repo root (default: cwd)")
    ap.add_argument("--check", action="store_true", help="dry run, no changes written")
    args = ap.parse_args()

    root = pathlib.Path(args.source).resolve()
    if not (root / "app" / "src" / "main").is_dir():
        print("error: %s doesn't look like a morphe-manager checkout (no app/src/main)" % root, file=sys.stderr)
        sys.exit(1)

    ws = Workspace(root)
    built, problems = build_plan(ws)
    if problems:
        for pr in problems:
            print("[!] " + pr, file=sys.stderr)
        sys.exit(1)
    plan, new_files, adv, needles = built

    failed = []
    status = {}
    for label, path, edits in plan:
        cur = ws.get(path)
        bad = []
        for desc, fn, optional in edits:
            r = fn(cur)
            if r is None:
                if optional:
                    note("%s: '%s' anchor not found, skipped" % (label, desc))
                else:
                    bad.append(desc)
            else:
                cur = r
        if bad:
            failed.append(label)
            print("[!] %s: no matching anchor for: %s" % (label, ", ".join(bad)), file=sys.stderr)
            status[path] = "failed"
            continue
        ws.set(path, cur)
        status[path] = "pending" if cur != ws.orig[path] else "applied"

    if not failed:
        fix_duplicate_strip_toggle(ws, adv)
        if ws.get(adv) != ws.orig[adv]:
            status[adv] = "pending"

    new_status = {}
    for path, text in new_files:
        old = path.read_text(encoding="utf-8").replace("\r\n", "\n") if path.is_file() else None
        ws.add_new(path, text)
        new_status[path] = "applied" if old == text else ("pending-new" if old is None else "pending")

    if failed:
        print("\n%d file(s) failed: %s\nnothing written." % (len(failed), ", ".join(failed)), file=sys.stderr)
        sys.exit(1)

    changed = [p for p, s in list(status.items()) + list(new_status.items()) if s.startswith("pending")]
    errors = verify(ws, changed, ws.main / "res" / "values" / "strings.xml", needles)
    if errors:
        for e in errors:
            print("[!] " + e, file=sys.stderr)
        print("\nnothing written.", file=sys.stderr)
        sys.exit(1)

    for p, s in list(status.items()) + list(new_status.items()):
        rel = p.relative_to(root)
        if s == "applied":
            print("[=] %s: already applied" % rel)
        elif args.check:
            print("[ ] %s: pending" % rel)
        else:
            ws.write(p)
            print("[+] %s: %s" % (rel, "created" if s == "pending-new" else "applied"))
    for n in NOTES:
        print("[~] " + n)

    if args.check:
        sys.exit(1 if changed else 0)
    print("\ndone.")


if __name__ == "__main__":
    main()
