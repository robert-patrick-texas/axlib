# Text-filter samples

These files were supplied with the early text-filter modules and are retained as
hands-on inputs outside the installable package source.

```text
text.test    #, ;, !, and // comments, quoted values, triple-quoted notes,
             blank and whitespace-only lines, and Python, JSON, INI, and CSV
text.md      Python-style text whose docstrings contain comment-like lines
text.white   tabs, repeated spaces, quoted regions, escaped quotes, and an
             unclosed quote
text.toml    TOML with # and ; inside quoted strings
text.yaml    indented YAML with quoted values and inline comments
```

Try them from the repository root with commands such as the ones below. In a
development checkout, run `uv sync` once and put `uv run` before each `python`.

```bash
python -m axlib.tf comments examples/text/text.test
python -m axlib.tf whitespace-safe examples/text/text.yaml
python -m axlib.tf comments examples/text/text.toml
python -m axlib.tf whitespace examples/text/text.white
python -m axlib.tf triple-quotes examples/text/text.md \
  | python -m axlib.tf slash-comments \
  | python -m axlib.tf comments
```

What to look for:

- `comments` on `text.test` removes the full-line and inline `#`, `;`, and `!`
  comments. It keeps the spaces before an inline comment, `123#not a comment`
  (no space before the `#`), quoted text such as `"Hello, world!"`, and the
  `//` lines, which `slash-comments` handles.
- `whitespace-safe` on `text.yaml` leaves quoted values alone but shrinks every
  indentation to one space, which breaks the YAML structure. The whitespace
  filters are not for indentation-sensitive formats.
- `comments` on `text.toml` removes only the full-line `#` comments; the `#` and
  `;` inside quoted strings stay.
- `whitespace` on `text.white` collapses tabs and repeated spaces outside quotes,
  while quoted regions keep theirs. `whitespace-safe` gives the same result for
  this file.
- The `text.md` pipeline removes both docstrings, every comment, and the
  `#!` first line. Run `triple-quotes` first: `comments` alone also removes the
  `;` and `!` lines inside the docstrings, whatever their labels say.

Notes inside the samples, such as `remove this` or `protected`, are labels for
the reader; `docs/TEXT_FILTERS.md` describes what each filter actually does.

Review each transformation before using the result as input to a deployment
system. The filters intentionally operate on text and do not validate a network
vendor's configuration grammar.
