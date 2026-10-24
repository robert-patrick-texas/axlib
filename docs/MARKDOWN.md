# Converting Markdown to HTML: axlib md2html

`axlib md2html` turns Markdown files into HTML pages that open in any
browser. Use it to:

- turn one runbook or change plan into a page to attach to a ticket or an
  e-mail;
- turn a folder of team notes into a small site, with a sidebar listing
  every page and the current page's sections;
- convert Markdown from a pipe, for example a report a script just wrote.

The Markdown is read the way GitHub reads it: a document that looks right on
GitHub looks the same in the HTML, and links between your Markdown files keep
working. This project's own documentation site is built with it (see
[Publishing a site](#publishing-a-site)).

## Install

```bash
uv add 'axlib[markdown]'
```

On a shared server installed with `install.sh`, the administrator adds it
with `sudo ./axlib-<version>/install.sh --with-markdown` (see
[INSTALL.md](INSTALL.md#options)). The `markdown` extra installs
`markdown-it-py`, `linkify-it-py`, and `mdit-py-plugins`, all small,
pure-Python packages. Without it, `axlib md2html` prints
`axlib md2html: markdown-it-py is not installed: uv add 'axlib[markdown]'`
and exits with status 2.

## Quick start

One file becomes one page beside it:

```console
$ axlib md2html runbooks/change-plan.md
runbooks/change-plan.md -> runbooks/change-plan.html
```

A folder becomes a site. `-r` includes subfolders, `-o` names the output
folder, and `--index` makes the folder's README the home page:

```console
$ axlib md2html -r runbooks -o site --index runbooks/README.md \
      --site-name "NOC runbooks" --link Wiki=https://wiki.example.net/noc
runbooks/change-plan.md:7: no section #roll-back in runbooks/core/upgrade.md
runbooks/README.md -> site/index.html
runbooks/change-plan.md -> site/change-plan.html
runbooks/core/upgrade.md -> site/core/upgrade.html
copied site/img/topology.png
```

The first line is a link problem: `change-plan.md` links to a `#roll-back`
section, but the heading in `upgrade.md` is "Rollback" (`#rollback`). The
pages are written anyway; `--strict` makes such problems fail the command.
`img/topology.png`, which `change-plan.md` shows as an image, is copied so
the site works on its own.

Markdown from a pipe becomes a page on standard output, or just the converted
HTML with `--fragment`:

```console
$ printf '# Shift notes\n\n- sw1 **drained**, see https://wiki.example.net/noc\n' | axlib md2html --fragment
<h1 id="shift-notes">Shift notes</h1>
<ul>
<li>sw1 <strong>drained</strong>, see <a href="https://wiki.example.net/noc">https://wiki.example.net/noc</a></li>
</ul>
```

## What is converted

GitHub-flavored Markdown, as on GitHub:

- everything in [CommonMark](https://commonmark.org/): headings, paragraphs,
  emphasis, lists, block quotes, code spans and fenced code blocks, links,
  images, and horizontal rules;
- tables, with column alignment;
- `~~strikethrough~~`;
- bare URLs such as `https://wiki.example.net/noc`, which become links;
- task lists (`- [ ] drain sw1`, `- [x] backup taken`), shown as check boxes
  that cannot be clicked;
- footnotes (`text[^1]` and `[^1]: the note`), listed at the end of the page.

Every heading gets an `id` made the way GitHub makes its anchors, so a link
such as `INSTALL.md#recorded-options` that works on GitHub also works in the
HTML. GitHub lowercases the heading text, removes punctuation other than
`-` and `_`, and turns each space into a hyphen; a repeated heading gets
`-1`, `-2`, and so on. "Scheduled jobs (cron and systemd)" becomes
`#scheduled-jobs-cron-and-systemd`.

HTML written inside the Markdown, such as `<details>`, is passed through, as
CommonMark specifies. Use `--escape-html` for Markdown from someone else, so
any HTML in it is shown as text instead (see [Security](#security)).

Not supported: emoji short codes (`:smile:` stays as typed), syntax
highlighting (code blocks are shown in one color; the language name is kept
as a `language-bash` class), and diagrams such as Mermaid (shown as code).

## What gets converted, and where the HTML goes

| Input | Default output | With `-o` |
| --- | --- | --- |
| No `PATH`, or `-` | A page on standard output | `-o FILE` writes the page to `FILE` |
| One file | `NAME.html` beside it | `-o FILE.html`, `-o DIR/` (an existing folder, or a name ending in `/`), or `-o -` for standard output |
| Several files, or a folder | Each page beside its Markdown | `-o DIR`: every page under `DIR`, in the same layout |

- A folder contributes its `.md` and `.markdown` files; `-r` adds those in
  its subfolders. Hidden files and folders (names starting with `.`) are
  skipped. A file named on the command line is converted whatever its name.
- The files keep their places relative to the folder they have in common:
  `axlib md2html README.md docs -o site` writes `site/README.html` and
  `site/docs/INSTALL.html`.
- The sidebar lists the pages in the order given; each folder's files are
  sorted by path. `--index FILE` writes `FILE` as `index.html` in its
  folder and lists it first. `FILE` must be one of the files converted.
- Files must be UTF-8 text. An HTML file that already exists where a page
  goes is replaced, so keep hand-written HTML under another name.
- With one file and `-o FILE` or `-o -`, links are rewritten as if the page
  were beside its Markdown, and linked files are not copied.
- `--fragment` (standard input or one file, written where that page would
  go) writes only the converted Markdown: no page layout, no styles, and no
  `#` link after each heading, ready to paste into another page or a wiki.
  The headings keep their ids.
- `--title` is for standard input, and `-r` and `--index` are for files;
  given with the other kind of input, they are refused rather than ignored.

## The page layout

Every page has:

- **A top bar**, when `--site-name` or `--link` is given: the site name at
  the left (a link to the `--index` page when there is one), and each
  `--link LABEL=URL` at the right. `--link` can be repeated, for example
  `--link GitHub=https://github.com/ORG/REPO --link PyPI=https://pypi.org/project/NAME/`.
- **A sidebar down the left**: every page converted together, titled by its
  first `#` heading (or its file name without the suffix when it has none),
  with the current page's `##` and `###` headings under it.
- **The document**, with a `#` link after each heading that appears on
  hover, for copying a link to that section.
- **A footer** naming the Markdown file, relative to the folder the files
  have in common. With `--source-url URL`, the name links to `URL` plus that
  path, for example the file on GitHub. A page from standard input has no
  footer.

The browser tab shows the page title, then the site name. The pages follow
the reader's light or dark system setting. On a phone, the document comes
first, with a "Pages and sections" link at the top that jumps to the
sidebar, which follows the document. Printing leaves out the bar and the
sidebar. The layout has no JavaScript and loads nothing from the internet:
the styles are written into each page, so a single page can be mailed or
opened from a file share and still look right.

## Links between pages

When several files are converted together, each link is rewritten and
checked:

- **A link to another converted file** points at its HTML page, keeping the
  section: `[install](docs/INSTALL.md#offline-hosts)` becomes
  `docs/INSTALL.html#offline-hosts`. A link to a folder opens its
  `index.md` or `README.md` page when one was converted, as GitHub shows a
  folder's README.
- **A `#section` link**, on the same page or another, must name a heading
  that exists there.
- **A link or image to any other local file** must exist. When the pages are
  written to another folder with `-o`, the file is copied there too, in the
  same place relative to the pages, so images keep working. Three kinds of
  target are reported instead of copied: a file outside the folder the
  Markdown files have in common, a hidden file or a file in a hidden folder
  (such as `.ssh/` or `.env`, which should never be published), and a folder
  with no converted `index.md` or `README.md`.
- **Links to web sites**, `mailto:` links, and paths starting with `/` are
  left alone.

A link that fails a check is printed to standard error as `file:line:
message`, with the line where the link's paragraph, list item, or table row
starts; the file name can be opened as printed. The page is still written.
Links inside HTML written in the Markdown (`<a href="...">`) are neither
rewritten nor checked.

## Options

| Option | Meaning |
| --- | --- |
| `PATH ...` | Markdown files and folders, in sidebar order. None, or `-`, reads standard input. |
| `-o`, `--output OUTPUT` | Where the HTML goes; see [the table above](#what-gets-converted-and-where-the-html-goes). |
| `-r`, `--recursive` | Also convert Markdown in the folders' subfolders. |
| `--index FILE` | Write `FILE` as `index.html` and list it first. |
| `--site-name NAME` | Name in the top bar and the browser tab. |
| `--link LABEL=URL` | A link in the top bar of every page. Repeatable. |
| `--source-url URL` | Base URL of the Markdown sources, for the footer link. |
| `--title TITLE` | Page title for standard input (default: the first `#` heading, else "Document"). |
| `--fragment` | Write only the converted Markdown (standard input or one file). |
| `--escape-html` | Show HTML in the Markdown as text. |
| `--strict` | Exit with status 1 when a link problem is found. |
| `--json` | Print a JSON report instead of the list of pages. |
| `-q`, `--quiet` | Do not list the pages written. |

The same command is available as `axlib-md2html` and
`python -m axlib.md2html`.

## Output and exit status

Each page written is listed on standard output as `SOURCE -> HTML`, and each
copied file as `copied PATH`, relative to the current folder when they are
under it. Nothing is listed when a page goes to standard output. `--json`
prints this report instead (problems are then in the report, not on standard
error); it cannot be combined with a page written to standard output:

```json
{
  "pages": [
    {
      "source": "runbooks/README.md",
      "output": "site/index.html"
    },
    {
      "source": "runbooks/change-plan.md",
      "output": "site/change-plan.html"
    },
    {
      "source": "runbooks/core/upgrade.md",
      "output": "site/core/upgrade.html"
    }
  ],
  "copied": [
    "site/img/topology.png"
  ],
  "problems": [
    {
      "source": "runbooks/change-plan.md",
      "line": 7,
      "url": "core/upgrade.md#roll-back",
      "message": "no section #roll-back in runbooks/core/upgrade.md"
    }
  ]
}
```

| Status | Meaning |
| --- | --- |
| 0 | Converted. Link problems, if any, were printed. |
| 1 | A link problem was found and `--strict` was given. The pages were still written. |
| 2 | Nothing was converted: a missing file, a folder with no Markdown files, a file that is not UTF-8, `markdown-it-py` not installed, an option that does not fit the input, or a file that could not be read or written. |

Without a `PATH`, the command reads standard input, so typed on its own it
waits for Markdown to be typed (end with `Ctrl+D`).

## Python API

```python
from axlib.md2html import SiteOptions, convert_paths, markdown_to_html

html = markdown_to_html("# Change plan\n\n- [ ] drain **sw1**")

report = convert_paths(
    ["runbooks"],
    "site",
    recursive=True,
    index="runbooks/README.md",
    options=SiteOptions(
        site_name="NOC runbooks",
        links=(("Wiki", "https://wiki.example.net/noc"),),
    ),
)
for problem in report.problems:
    print(problem)  # runbooks/change-plan.md:7: no section #roll-back in ...
```

| Name | Purpose |
| --- | --- |
| `markdown_to_html(text, *, escape_html=False)` | Markdown to an HTML fragment, as `--fragment` writes it. |
| `convert_text(text, *, title=None, options=None)` | Markdown to a complete page, as from standard input. |
| `convert_paths(paths, output_dir=None, *, recursive=False, index=None, options=None)` | Convert and write files and folders; returns a `ConversionReport`. |
| `build_site(paths, *, output_dir=None, recursive=False, index=None, options=None)` | The same, without writing: returns a `Site` with each page's `html` and `body`, the files to copy, and the problems. |
| `write_site(site, output_dir=None)` | Write a `Site`; returns a `ConversionReport`. |
| `SiteOptions(site_name=None, links=(), source_url=None, escape_html=False)` | Settings for every page. `links` is a tuple of `(label, url)` pairs. |
| `ConversionReport` | `pages` (`(source, output)` pairs), `copied`, `problems`, and `to_dict()` for JSON. |
| `LinkProblem` | `source`, `line`, `url`, `message`; `str()` gives `file:line: message`. |
| `parse_markdown(text, *, escape_html=False)` | A `Document`: tokens, `headings`, `title`, and `slugs`. |
| `Heading` | `level`, `text`, `slug`. |
| `slugify(text)` | A heading's GitHub anchor. |
| `render_page(...)`, `NavEntry` | The page layout, for building pages yourself. |
| `Md2HtmlError`, `MarkdownDependencyError` | Raised when the input cannot be converted, or `markdown-it-py` is missing. |

Every function that parses Markdown raises `MarkdownDependencyError` when
the `markdown` extra is not installed. `convert_paths` and `build_site` raise
`Md2HtmlError` for a missing path, a folder with no Markdown files, a file
that is not UTF-8, an `--index` file that is not converted, or two files
that would become the same page (`README.md` given as `--index` next to an
`index.md`); they raise `OSError` when a file cannot be read or written.

## Publishing a site

The pages need no server-side software: copy the output folder to any web
server, or open `index.html` from a file share. Links between pages are
relative, so the site works from any folder or URL path.

This project publishes its own documentation this way. `make docs` runs:

```bash
axlib md2html --strict --index README.md -o site \
    --site-name "axlib $(uv version --short)" \
    --link GitHub=https://github.com/robert-patrick-texas/axlib \
    --link PyPI=https://pypi.org/project/axlib/ \
    --source-url https://github.com/robert-patrick-texas/axlib/blob/main/ \
    README.md docs/INSTALL.md ... CHANGELOG.md
```

`.github/workflows/docs.yml` runs `make docs` from each release's tag and
deploys `site/` to GitHub Pages, at
<https://robert-patrick-texas.github.io/axlib/>. `--strict` stops the
publication if any link is broken. [DEVELOPMENT.md](DEVELOPMENT.md#documentation-site)
describes the setup.

## Security

- HTML written in the Markdown is passed through, so a page converted from
  someone else's Markdown could carry their scripts. Use `--escape-html`
  (`escape_html=True` in Python) for Markdown you did not write.
- Links with `javascript:`, `vbscript:`, `file:`, and `data:` URLs (other
  than PNG, JPEG, GIF, and WebP images) are never made into links or
  images, with or without `--escape-html`; they are shown as typed.
- The pages contain no JavaScript of their own and load nothing from the
  internet except what the Markdown itself points at, such as images.
- Only files inside the folder the Markdown files have in common are copied
  next to the pages, and never hidden ones. The footer shows paths relative
  to that folder, so a published page does not reveal where its files were
  on your computer.

## Troubleshooting

| Message | Cause and fix |
| --- | --- |
| `markdown-it-py is not installed: uv add 'axlib[markdown]'` | The `markdown` extra is missing. Run the command shown, or ask the administrator to rerun `install.sh --with-markdown`. |
| `FILE:LINE: no section #NAME on this page` | No heading on the page has that anchor, often because a heading was renamed. Anchors follow [GitHub's rules](#what-is-converted); the `#` link that appears on hover beside a heading in the page shows the right one. |
| `FILE:LINE: no section #NAME in OTHER.md` | The same, for a heading in another converted file. |
| `FILE:LINE: PATH does not exist` | The link or image points at a missing file. Paths are relative to the Markdown file containing the link. |
| `FILE:LINE: PATH is outside FOLDER, so it is not copied` | A link reaches above the folder the files have in common, so the copy in the output folder will be missing. Move the file under that folder, or convert from a higher folder. |
| `FILE:LINE: PATH is hidden, so it is not copied` | The link points into a hidden file or folder. Move what you mean to publish out of it, or link to it on a web server instead. |
| `FILE:LINE: PATH is a folder with no converted index.md or README.md` | A folder link works on disk but has no page in the site. Add a `README.md` to the folder and convert it too, or link to a file inside it. |
| `A and B would both become index.html` | `--index README.md` is used next to an `index.md` (or two files share a name and folder). Rename one, or choose another `--index`. |
| `FILE: not UTF-8 text` | The file was saved in another encoding. Convert it: `iconv -f latin1 -t utf-8 FILE > FILE.new`. |
| `several pages cannot be written to standard output` | `-o -` takes one page. Give an output folder instead. |
| `--fragment needs standard input or one file` | A fragment has no layout to hold several pages. |
| `--title is for standard input; ...`, `-r and --index need files, ...`, `--json needs -o FILE: ...`, `--json cannot be used with -o -: ...` | The option does not apply to this kind of input or output; see [the table above](#what-gets-converted-and-where-the-html-goes). |
| A bare URL stays plain text, or `- [ ]` shows as text | `linkify-it-py` or `mdit-py-plugins` is missing from the environment: reinstall with `uv add 'axlib[markdown]'`. |
| The command waits and prints nothing | With no `PATH`, it reads standard input. Give a file, or press `Ctrl+D`. |
