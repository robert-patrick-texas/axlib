# Text filters

Axlib text filters are pure string transformations. They do not log in to devices
or execute commands. This makes them useful teaching examples and safe building
blocks for preparing configuration text before a separate, reviewed transport
layer sends anything to a network device.

## Consistent CLI

Every filter accepts:

```text
INPUT                  input path, or - for standard input
-o, --output OUTPUT    output path, or - for standard output
--encoding ENCODING    text encoding, default utf-8
```

Use unified commands:

```bash
python -m axlib.tf COMMAND [COMMAND OPTIONS] [INPUT] [-o OUTPUT]
```

or direct modules:

```bash
python -m axlib.tf.rmcomment INPUT -o OUTPUT
```

Installed packages also provide `axlib-tf` and individual `axlib-tf-*` scripts.

## Python naming convention

Each module preserves its descriptive original function and also exposes the
alias `transform_text`:

```python
from axlib.tf.rmcomment import transform_text

cleaned = transform_text(raw_configuration)
```

The alias provides a consistent name for generic pipelines. Use the descriptive
name when code benefits from an explicit intent.

## Filters

### `include` / `incfile.py`

Expands `$include`, `#include`, `@include`, and `@import` lines by default.
Filename matching is case-insensitive and accepts unquoted, single-quoted, or
double-quoted paths. Relative paths inside an included file are resolved from
that file's directory.

Recursive expansion is on by default. `--no-recursive`, `--max-depth`, and
cycle detection prevent unbounded recursion. A missing file, blocked cycle, or
nested directive beyond the depth limit remains visible as its original line.

When a named input file is used, initial relative paths resolve from its parent.
For standard input, they resolve from the current directory unless `--base-dir`
is supplied.

### `comments` / `rmcomment.py`

Removes comments started by `;`, `!`, and `#` outside single or double quotes.
A marker starts a comment only at the beginning of a line or after whitespace;
this protects values such as `description "WAN #1"`. Backslash escaping is
honored. Repeated `-c/--comment-char` options select a custom marker set.

### `slash-comments` / `rmdouble.py`

Removes a whole line when its first non-whitespace characters are `//`. It does
not remove inline `//`, so URLs remain intact.

### `triple-quotes` / `rmtriple.py`

Removes balanced triple-single-quoted and triple-double-quoted spans, including
multiline spans. This is intentionally a text operation. It also removes valid
assigned multiline Python strings. Use Python's `ast` or `tokenize` modules when
the task is specifically to remove Python docstrings.

### `lines` / `rmline.py`

Strips both line edges by default. Options select leading-only or trailing-only
behavior and can retain or collapse blank lines. `--preserve-line-endings`
retains LF, CRLF, and CR exactly; otherwise retained lines use LF.

Stripping whitespace is useful before sending a command list because accidental
leading spaces can change parser behavior on some systems. It must not be used
blindly on indentation-sensitive formats such as Python or YAML.

### `whitespace` / `rmwhite.py`

Converts tabs to spaces and collapses repeated spaces outside straight single or
double quotes. Quote state is line-scoped, so a malformed line cannot protect all
remaining configuration text.

### `whitespace-safe` / `rmwhite2.py`

Uses a more cautious balanced-quote heuristic. It protects straight double
quotes, curly single quotes, and straight single quotes only when a closing quote
exists on the same line. It is useful when apostrophes appear in prose.

### `variables` / `varsub.py`

Replaces `<var>name</var>` tokens case-insensitively. Replacement values retain
their original case and whitespace. Unrecognized tags are left unchanged.
Repeated `-v/--var NAME=VALUE` options supply values.

Do not place passwords in template command-line arguments because shell history
and process inspection can expose them. Use the credential API inside Python for
secret values.

## Pipeline example

```bash
python -m axlib.tf include templates/site.conf \
  | python -m axlib.tf comments --preserve-line-endings \
  | python -m axlib.tf variables \
      -v hostname=edge-01 \
      -v management_ip=192.0.2.10 \
  | python -m axlib.tf whitespace \
  > rendered/edge-01.conf
```

Always review or diff generated output before a separate deployment tool sends
commands to production devices.
