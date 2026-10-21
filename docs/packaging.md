# Packaging AWS 95 Tools

Two ways to run the app. Know which one you are in.

| | Source launchers | Packaged build |
|---|---|---|
| Files | `run.sh`, `AWS95Tools.command`, `AWS95Tools.bat`, in-repo `AWS95Tools.app` | `dist/` (gitignored) |
| Runs | the `aws95` package from this checkout, through `uv` | a Nuitka standalone build, no Python or uv needed |
| Deps | `uv sync` first (the launchers do it) | bundled |
| Use for | development and the owner's daily use | handing the app to a machine without the repo |

The source launchers stay (ruling M4-R1, a deliberate deviation from spec section 3).

## Build (current OS)

```bash
uv sync                              # includes the dev dependency nuitka
uv run python -m tools.build_nuitka  # prints the Nuitka command, then runs it; output in dist/
```

`tools/build_nuitka.py` builds the command line: `build("macos" | "windows", onefile=False)`.
It uses `--standalone`, `--enable-plugin=pyside6`, `--include-package=aws95` and `--include-package-data=aws95`
(sounds, icons, demo fixtures), plus `--include-package=botocore` and `--include-package-data=botocore` / `boto3`
(the botocore service models are JSON data files; without them every AWS client fails in the packaged app), excludes `pytest`, `tests` and `tools`, and takes the product name and version from
`aws95.__version__` (a `.devN` suffix is dropped because Nuitka needs a numeric version) and the entry file `AWS 95 Tools.py`, whose name becomes the app's file name.
It also passes `--assume-yes-for-downloads`, which lets Nuitka download tools it needs (for example a C compiler or
dependency walker) without asking.

The build needs a C compiler: Xcode Command Line Tools on macOS, MSVC (Visual Studio Build Tools) or MinGW64 on Windows.
On macOS, Apple's `install_name_tool` must come first on `PATH`. Some Python distributions (Anaconda, for example) ship their own, and the build then fails at the last step with `call to 'install_name_tool' gave warnings`. Run the build as `PATH="/usr/bin:$PATH" uv run python -m tools.build_nuitka` in that case.
It takes several minutes.

### macOS

Produces `dist/AWS 95 Tools.app`. Nuitka names the bundle after the entry file `AWS 95 Tools.py` at the repo root; `python -m aws95` keeps using `aws95/__main__.py`. The build command fails if the bundle comes out under any other name.
Smoke-run it:

```bash
EXE="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "dist/AWS 95 Tools.app/Contents/Info.plist")"
"dist/AWS 95 Tools.app/Contents/MacOS/$EXE" --version     # prints: AWS 95 Tools <version>
"dist/AWS 95 Tools.app/Contents/MacOS/$EXE" --demo
```

### Windows (run on a Windows machine; this repo's dev machine cannot build it)

```bat
uv sync
uv run python -m tools.build_nuitka
"dist\AWS 95 Tools.dist\AWS 95 Tools.exe" --version
"dist\AWS 95 Tools.dist\AWS 95 Tools.exe" --demo
```

Equivalent explicit command: `uv run python -c "from tools.build_nuitka import build; print(*build('windows'))"`
prints the exact Nuitka argument list. Add `onefile=True` in `build()` for a single `.exe`; startup is slower because
it unpacks on every launch, so prefer the standalone folder.

## Measuring boot

`AWS95_EXIT_AFTER_BOOT_MS=<n>` makes the app quit with exit code 0 `<n>` ms after the shell's first paint, and print
`AWS95_BOOT_MS=<ms>`: the time from the start of `main()` to that first paint. Unset, it changes nothing.

- The boot metric is first paint, taken while the shell is still fading in; it is not the end of the fade.
- A measurement run saves window geometry like a normal quit. Use a scratch `HOME` if that matters.

```bash
AWS95_EXIT_AFTER_BOOT_MS=0 "dist/AWS 95 Tools.app/Contents/MacOS/$EXE" --demo   # EXE as in the macOS section
```

Do not commit anything from `dist/`, `*.build/` or `*.dist/`.
