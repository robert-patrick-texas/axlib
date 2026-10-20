# Text filters

Axlib text filters are string transformations that use only the Python standard
library. They do not log in to devices or execute commands, and the only files
they open are the input, the output, and the files that `include` expands. This
makes them useful teaching examples and safe building blocks for preparing
configuration text before a separate, reviewed transport layer sends anything
to a network device.

## Consistent CLI

Every filter accepts:

```text
INPUT                  input path, or - for standard input (the default)
-o, --output OUTPUT    output path, or - for standard output (the default)
--encoding ENCODING    text encoding, default utf-8
```

`--encoding` is used to read the input, to write the output, and, for
`include`, to read the included files. Input is read as bytes and decoded
without newline translation, so each filter sees the original LF, CRLF, or CR
line endings; each filter's section says what it does with them.

Use unified commands:

```bash
axlib tf COMMAND [COMMAND OPTIONS] [INPUT] [-o OUTPUT]
python -m axlib.tf COMMAND [COMMAND OPTIONS] [INPUT] [-o OUTPUT]
```

or direct modules:

```bash
python -m axlib.tf.rmcomment INPUT -o OUTPUT
```

Installed packages also provide `axlib-tf`, which works like `axlib tf`, and an
individual `axlib-tf-*` script for each filter. `axlib tf --help` lists the
commands; `--help` after a command lists its options.

| Command | Script | Module | Python function |
| --- | --- | --- | --- |
| `include` | `axlib-tf-include` | `axlib.tf.incfile` | `expand_includes` |
| `comments` | `axlib-tf-comments` | `axlib.tf.rmcomment` | `strip_comments` |
| `slash-comments` | `axlib-tf-slash-comments` | `axlib.tf.rmdouble` | `remove_double_slash` |
| `triple-quotes` | `axlib-tf-triple-quotes` | `axlib.tf.rmtriple` | `remove_triple_quoted` |
| `lines` | `axlib-tf-lines` | `axlib.tf.rmline` | `normalize_text` |
| `whitespace` | `axlib-tf-whitespace` | `axlib.tf.rmwhite` | `normalize_spaces_outside_quotes` |
| `whitespace-safe` | `axlib-tf-whitespace-safe` | `axlib.tf.rmwhite2` | `normalize` |
| `variables` | `axlib-tf-variables` | `axlib.tf.varsub` | `substitute_variables` |

The exit status is `0` on success. A file that cannot be read or written, input
that cannot be decoded, an unknown encoding, or a closed output pipe prints a
one-line error to standard error and exits `1`. Invalid options and an unknown
command print a usage message and exit `2`. `comments` and `include` also
report input that cannot be decoded that way, with status `2`.

## Python naming convention

Each module preserves its descriptive original function and also exposes the
alias `transform_text`:

```python
from axlib.tf.rmcomment import transform_text

cleaned = transform_text(raw_configuration)
```

The alias provides a consistent name for generic pipelines. Use the descriptive
name when code benefits from an explicit intent. The descriptive names can also
be imported from the package, which loads each module only when its function
is first used. There, `axlib.tf.rmwhite2.normalize` is named
`normalize_whitespace_safely`, a name the module also provides:

```python
from axlib.tf import normalize_whitespace_safely, strip_comments
```

Each function takes the text first and returns the transformed string:

```text
expand_includes(text, *, directives=None, recursive=True, max_depth=20,
                encoding="utf-8", base_dir=None)
strip_comments(text, *, comment_chars=(";", "!", "#"), preserve_line_endings=True)
remove_double_slash(text)
remove_triple_quoted(text)
normalize_text(text, mode="both", *, keep_original_blank_lines=False,
               collapse_blank_lines=False, preserve_line_endings=False)
normalize_spaces_outside_quotes(text)
normalize(text)
substitute_variables(text, variables=None)
```

An invalid argument, such as a negative `max_depth`, a blank directive, a
comment marker that is not exactly one character, or an unknown `mode`, raises
`ValueError`. `expand_includes`
also raises `OSError` or `UnicodeDecodeError` when an include file exists but
cannot be read or decoded. Note that `strip_comments` keeps line endings by
default, while the `comments` command converts them to LF unless given
`--preserve-line-endings`.

## Filters

### `include` / `incfile.py`

Expands `$include`, `#include`, `@include`, and `@import` lines by default. A
directive line starts, after optional indentation, with the directive word,
followed by whitespace and a path. The whole line, including its indentation
and line ending, is replaced by the file's content exactly as read, so the
included file keeps its own line endings. An included file should end with a
newline; otherwise its last line and the line after the directive run together.

Directive matching is case-insensitive (`@INCLUDE` works), but the path is used
as written, so on Linux its case must match the file name. The path may be
unquoted, single-quoted, or double-quoted; quote a path that contains spaces.
`~` expands to the home directory, and anything after the path and a space is
ignored, so `@include ntp.conf  # shared` works. Repeated `-d/--directive WORD`
options replace the default directive words.

Relative paths inside an included file are resolved from that file's directory.
When a named input file is used, initial relative paths resolve from its parent.
For standard input, they resolve from the current directory. `--base-dir DIR`
overrides both. In Python, `expand_includes` resolves them from `base_dir`,
which defaults to the current directory.

Recursive expansion is on by default. `--no-recursive`, `--max-depth`, and
cycle detection prevent unbounded recursion. `--no-recursive` inserts each
directly included file without expanding the directives inside it.
`--max-depth N` (default 20) allows N levels of nested includes below the
direct ones, so `--max-depth 0` works like `--no-recursive`; a negative value
exits with status `2`. A missing file, blocked cycle, or nested directive beyond
the depth limit remains visible as its original line. An include path that
exists but cannot be read, such as a directory, stops the command with an error.

Run `include` before `comments`, because `comments` removes `#include` lines.

### `comments` / `rmcomment.py`

Removes comments started by `;`, `!`, and `#` outside single or double quotes.
A marker starts a comment only at the beginning of a line or after whitespace.
The marker and the rest of its line are removed; whitespace before the marker
stays. A line that held only a comment, indented or not, is removed, while
blank and whitespace-only lines are kept.

Quotes protect values such as `description "WAN #1"`, and the whitespace rule
protects `community#literal` and URLs such as `http://host/page#top`. Quote state
is tracked per line, and an unclosed quote protects the rest of its line. An
apostrophe also counts as a quote, so in `description don't # note` the comment
is kept. Backslash escaping is honored: `\"` and `\'` do not open or close a
quoted region, and the backslash stays in the output.

Repeated `-c/--comment-char CHAR` options select a custom marker set that
replaces the defaults. Each marker must be a single character.

The command writes LF line endings, and the output ends with a newline only if
the input did. `--preserve-line-endings` retains LF, CRLF, and CR exactly. In
Python, `strip_comments` retains them by default; pass
`preserve_line_endings=False` for LF.

### `slash-comments` / `rmdouble.py`

Removes a whole line, including its line ending, when its first non-whitespace
characters are `//`. It does not remove inline `//`, so URLs and trailing
`// notes` remain intact. Other lines and their line endings are unchanged. It
has no options besides the common ones.

### `triple-quotes` / `rmtriple.py`

Removes balanced triple-single-quoted and triple-double-quoted spans, including
multiline spans and the quotes themselves. Text before and after a span stays,
joined on one line when the span crossed lines, and a line left blank by a
removal is dropped. Inside a span, a backslash escapes the next character. An
unclosed triple quote is left unchanged, and line endings outside spans are
preserved. It has no options besides the common ones.

This is intentionally a text operation. It also removes valid assigned
multiline Python strings. Use Python's `ast` or `tokenize` modules when the
task is specifically to remove Python docstrings.

Multiline spans are found only across LF line breaks. In CRLF text a multiline
span is not removed, and the LF of each CRLF inside it becomes a space, so
convert such text to LF first. Spans on a single line are removed normally.

### `lines` / `rmline.py`

Strips whitespace from both line edges by default. `--leading-only` or
`--trailing-only` strips one edge instead; the two cannot be combined.

Blank lines, including lines that become empty after stripping, are removed by
default. `--collapse-blank-lines` keeps one empty line for each run of blank or
whitespace-only lines. `--keep-original-blank-lines` keeps lines that were
already empty and still removes whitespace-only lines; it works only in the
default both-edge mode, and `--collapse-blank-lines` takes precedence over it.

`--preserve-line-endings` retains LF, CRLF, and CR exactly; otherwise retained
lines use LF, and the output ends with a newline only if the input did. In
Python, `normalize_text` selects the edge with `mode="both"`, `"leading"`, or
`"trailing"`.

Stripping whitespace is useful before sending a command list because accidental
leading spaces can change parser behavior on some systems. It must not be used
blindly on indentation-sensitive formats such as Python or YAML.

### `whitespace` / `rmwhite.py`

Converts each tab to a space and collapses each run of spaces to one space
outside straight single or double quotes. This includes indentation and
trailing whitespace, which each shrink to one space rather than disappearing,
so it is not for indentation-sensitive formats either. Text from a quote to its
closing quote is unchanged, and inside it a backslash escapes the next
character. Line endings are preserved.

Quote state is line-scoped, so a malformed line cannot protect all remaining
configuration text: an unclosed quote protects only the rest of its line. Every
`'` counts as a quote, so an apostrophe, as in `don't`, protects the text after
it up to the next `'` on that line. Curly quotes are ordinary characters.

### `whitespace-safe` / `rmwhite2.py`

Makes the same changes as `whitespace`, indentation included, but uses a more
cautious balanced-quote heuristic. It protects straight double quotes and curly
single quotes (`‘…’`) only when the closing quote is on the same line. A
straight single quote starts a protected region only at the start of a line or
after a space, tab, or `=`, and only when another `'` follows on the same line,
so apostrophes inside words are ordinary text. A backslash keeps the next
character from closing a region. Unclosed quotes and curly double quotes
protect nothing. It is useful when apostrophes appear in prose. Line endings
are preserved.

### `variables` / `varsub.py`

Replaces `<var>name</var>` tokens case-insensitively: the tag and the name may
use any case, so `<VAR>HostName</VAR>` takes the value of `hostname`. The name
must otherwise match exactly; `<var> hostname </var>` does not. Replacement
values retain their original case and whitespace. Unrecognized tags are left
unchanged, the text is unchanged when no values are supplied, and line endings
are preserved.

Repeated `-v/--var NAME=VALUE` options supply values. The value is everything
after the first `=`, so it may contain `=`. Spaces around NAME are ignored, and
when a name is given more than once, in any case, the last value wins. An
assignment without `=` or with an empty name exits with status `2`. In Python,
pass a mapping such as `{"hostname": "edge-01"}`;
`parse_variable_assignments(["hostname=edge-01"])` builds one from `NAME=VALUE`
strings.

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
