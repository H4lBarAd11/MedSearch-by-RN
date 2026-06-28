# Copyright 2026 Riccardo Nevoso
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
py2app build script for the MedSearch menu-bar app.

This creates a PROPER standalone macOS .app with Python embedded — unlike the
shell-script launcher approach, the bundle launches as its own process with full
GUI access, so the status-bar icon appears reliably, it runs headless (no Dock
icon, via LSUIElement), and it survives closing the terminal.

BUILD (run on your Mac, with Homebrew Python that has rumps + py2app):
    /opt/homebrew/bin/python3 -m pip install py2app --break-system-packages
    /opt/homebrew/bin/python3 setup.py py2app

The finished app appears in  ./dist/MedSearch Menu Bar.app
Move it to /Applications and/or add it to Login Items.

NOTE: do NOT build this from inside a virtualenv — rumps/py2app have known
issues with venv Pythons. Use the Homebrew python3 directly (as above).
"""
from setuptools import setup

APP = ['menubar.py']

# Files bundled into the app's Resources. The menu-bar glyph must be here so
# menubar.py can find it; we also bundle app.py + templates so the menu-bar app
# can launch the main MedSearch app even if run from /Applications.
DATA_FILES = [
    'menubar_icon.png',
    'app.py',
    'VERSION',
    ('templates', ['templates/index.html']),
]

OPTIONS = {
    'argv_emulation': False,            # we don't read argv; keep it simple/robust
    'iconfile': 'menubar_app_icon.icns',  # the books+network bundle icon
    'plist': {
        'CFBundleName': 'MedSearch Menu Bar',
        'CFBundleDisplayName': 'MedSearch Menu Bar',
        'CFBundleIdentifier': 'com.riccardonevoso.medsearch.menubar',
        'CFBundleShortVersionString': '1.3',
        'CFBundleVersion': '1.3',
        # Background app: no Dock icon, no app-switcher entry — just the menu bar.
        'LSUIElement': True,
        'LSMinimumSystemVersion': '10.13',
    },
    'packages': ['rumps'],
    # pyobjc bits rumps needs; py2app usually picks these up, listed for safety.
    'includes': ['AppKit', 'Foundation', 'objc'],
}

setup(
    app=APP,
    name='MedSearch Menu Bar',
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
)
