# Third-Party Notices

Sitesync is licensed under the MIT License (see `LICENSE`).

This project depends on third-party open source software. Licenses and attributions for
key runtime dependencies are summarized below. This list is provided for convenience
and may not include every transitive dependency.

For the authoritative dependency set and exact versions, see `pyproject.toml` and `uv.lock`.

## Runtime dependencies

- **beautifulsoup4** — MIT License — https://www.crummy.com/software/BeautifulSoup/
- **click** — BSD 3-Clause License — https://palletsprojects.com/p/click/
- **importlib-metadata** — Apache License 2.0 — https://github.com/python/importlib_metadata
- **playwright** — Apache License 2.0 — https://github.com/microsoft/playwright-python
  - Playwright downloads and uses browser engines (Chromium/Firefox/WebKit) which have their own licenses.
    See Playwright documentation for details.
- **pydantic** — MIT License — https://github.com/pydantic/pydantic
- **python-dotenv** — BSD 3-Clause License — https://github.com/theskumar/python-dotenv
- **pyyaml** — MIT License — https://pyyaml.org/
- **rich** — MIT License — https://github.com/Textualize/rich
- **tenacity** — Apache License 2.0 — https://github.com/jd/tenacity
- **typer** — MIT License — https://github.com/fastapi/typer

## Build and distribution tooling

- **PyInstaller** — GPLv2-or-later with a special exception — https://github.com/pyinstaller/pyinstaller

## Notes

- If you redistribute Sitesync in bundled form (for example, via the PyInstaller target),
  you are responsible for ensuring the applicable third-party license texts and notices
  are included in your distribution.

## Playwright NOTICE

The Playwright Python distribution includes a `NOTICE` file. At the time this repository was prepared, it contained:

```
Playwright
Copyright (c) Microsoft Corporation

This software contains code derived from the Puppeteer project (https://github.com/puppeteer/puppeteer),
available under the Apache 2.0 license (https://github.com/puppeteer/puppeteer/blob/master/LICENSE).
```
