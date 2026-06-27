"""
py2app build script for the MAIN MedSearch app (the search window).

This makes MedSearch.app fully self-contained: Python, Flask, pywebview, the
templates, and the icon are all embedded. Unlike the old shell-launcher build,
it doesn't depend on your project folder staying put or on which `python3`
resolves — you can move it anywhere, including /Applications.

BUILD (on your Mac, with the Homebrew Python that has flask + pywebview + py2app):
    /opt/homebrew/bin/python3 -m pip install py2app --break-system-packages
    /opt/homebrew/bin/python3 setup_main.py py2app

The finished app appears in  ./dist/MedSearch.app
Move it to /Applications.

NOTE: build with the Homebrew python3 directly, NOT from inside a virtualenv
(py2app has known issues with venvs).

This is SEPARATE from setup.py, which builds the menu-bar companion. Keep both.
"""
from setuptools import setup

APP = ['app.py']

# Bundle the template(s), the VERSION file, and the icon source into the app so
# the frozen build can find them at runtime (app.py is py2app-aware and looks in
# Contents/Resources).
DATA_FILES = [
    ('templates', ['templates/index.html']),
    'VERSION',
]

OPTIONS = {
    'argv_emulation': False,
    'iconfile': 'icon.icns',
    'plist': {
        'CFBundleName': 'MedSearch',
        'CFBundleDisplayName': 'MedSearch',
        'CFBundleIdentifier': 'com.riccardonevoso.medsearch',
        'CFBundleShortVersionString': '1.3',
        'CFBundleVersion': '1.3',
        'LSMinimumSystemVersion': '10.13',
        # The main app is a normal windowed app: it SHOULD have a Dock icon and
        # appear in the app switcher, so we do NOT set LSUIElement here.
        'NSHighResolutionCapable': True,
    },
    # Everything the app imports at runtime. pywebview + flask are the big ones;
    # we name pyobjc bits pywebview uses on macOS so they're pulled in.
    'packages': ['flask', 'webview', 'jinja2', 'werkzeug', 'click',
                 'markupsafe', 'itsdangerous'],
    'includes': ['webview.platforms.cocoa', 'objc', 'AppKit', 'Foundation',
                 'WebKit', 'urllib.request', 'urllib.parse', 'xml.etree.ElementTree'],
}

setup(
    app=APP,
    name='MedSearch',
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
)
