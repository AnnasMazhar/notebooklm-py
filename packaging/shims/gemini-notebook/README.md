# gemini-notebook

**Alias for [`gemini-notebook-py`](https://pypi.org/project/gemini-notebook-py/).**

This package ships no code of its own; it installs `gemini-notebook-py`.

Nothing about your code changes:

```python
import notebooklm  # permanent — the import name is not being renamed
```

Every extra is mirrored, so `pip install "gemini-notebook[mcp]"` installs exactly what
`pip install "gemini-notebook-py[mcp]"` installs.

## If you use pipx or `uv tool install`

This shim declares no console scripts (two distributions owning the same
commands would corrupt each other on uninstall). Isolated tool installers only
expose the requested distribution's entry points, so:

- `uvx --from "gemini-notebook[mcp]" notebooklm-mcp` — works.
- `pipx install gemini-notebook` — add `--include-deps` to get the commands, or
  install `gemini-notebook-py` directly (recommended).

## Upgrading from a pre-0.9 install

`gemini-notebook <= 0.8` shipped the `notebooklm` package files directly and collides
with `gemini-notebook-py`. If you have both:

```bash
pip uninstall notebooklm-py && pip install --force-reinstall gemini-notebook-py
```

`--force-reinstall` is required: uninstalling the old distribution deletes the
shared files, and a plain upgrade would consider the current install satisfied
and never restore them.

Current version: 0.9.0a1
