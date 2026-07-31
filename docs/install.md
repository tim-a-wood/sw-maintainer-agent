# Install and update

One script does the first install and every update. It is idempotent:
run it again at any time, and it converges to a good state.

## Install day

1. Install Python 3.11 or later from python.org, with the "py launcher"
   option.
2. Clone or copy this repository to the computer.
3. Open PowerShell in the repository folder.
4. Run:

       .\scripts\setup.ps1

5. Open a new terminal. Start the app with:

       maintain-ui

## Update day

1. Update the repository copy (`git pull`, or copy the new version).
2. Run the same script again:

       .\scripts\setup.ps1

The script updates the Maintain portion. Manim stays at the pinned
version until a Maintain release moves the pin; the same script then
updates both together.

## What the script does

`setup.ps1` only finds Python and starts `scripts/setup.py`; every
decision lives in the Python script, where the test suite exercises it
(`tests/test_setup_script.py`).

1. Confirms Python 3.11 or later.
2. Picks the Python for the app. The video feature (Manim) needs
   Python 3.11 to 3.13 — its native dependencies have no wheels for
   3.14 yet. When the default Python is 3.14 but an older supported
   one is installed, the script uses that one. When only 3.14 exists,
   the app installs without the video feature and the script says how
   to enable it later (install Python 3.13, run the script again).
3. Installs or updates pipx, the isolated app installer.
4. Installs Maintain with the `ui` and `explain` extras:
   `pipx install --force <repo>[ui,explain]`. If the full install
   fails, the script retries with `ui` alone so the app still lands.
5. Installs ffmpeg with winget when it is absent. Manim needs it for
   the video files; it can never come from pip.
6. Verifies the result and prints one PASS line for each step.

## Manual fallback

The script wraps four commands. When you prefer them by hand:

    py -3 -m pip install --user --upgrade pipx
    py -3 -m pipx ensurepath
    py -3 -m pipx install --force ".[ui,explain]"
    winget install ffmpeg

## Notes

- The app itself never installs software at run time. When Manim is
  absent, the render step says so and names the commands above.
- The Manim command that the app runs is a per-user setting:
  Settings → Explain. The default is `manim`.
