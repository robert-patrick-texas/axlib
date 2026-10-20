# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Tests for finding TextFSM templates and for axlib parse settings."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from axlib.parse.errors import (
    AmbiguousCommandError,
    ParseConfigurationError,
    ParseDependencyError,
    TemplateNotFoundError,
)
from axlib.parse.settings import TemplateSource, load_parse_settings
from axlib.parse.templates import (
    TemplateCatalog,
    bundled_template_dir,
    expand_completion,
    main,
    normalize_command,
    read_index,
    spell_out,
)

pytestmark = pytest.mark.usefixtures("clean_parse_environment")

EXAMPLE_TEMPLATES = Path(__file__).resolve().parents[1] / "examples/parse/templates"
# Enough of a template for lookup tests; they never run it.
TEMPLATE = "Value NAME (\\S+)\n\nStart\n  ^${NAME} -> Record\n"


def write_templates(directory: Path, *names: str, index: str | None = None) -> Path:
    """Create template files, and an index when given, in ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / name).write_text(TEMPLATE, encoding="utf-8")
    if index is not None:
        (directory / "index").write_text(index, encoding="utf-8")
    return directory


@pytest.fixture(scope="module")
def bundled() -> TemplateCatalog:
    """The ntc-templates collection alone."""
    return TemplateCatalog.load(environ={})


def test_completion_matches_clitable() -> None:
    assert expand_completion("sh[[ow]] ver[[sion]]") == "sh(o(w)?)? ver(s(i(o(n)?)?)?)?"
    assert spell_out("sh[[ow]] c[[dp]] neig[[hbors]]") == "show cdp neighbors"
    assert spell_out(r"sh[[ow]] int[[erfaces]](?: \S+)? st[[atus]]") is None
    assert normalize_command("  show   lldp neighbors |  no-more ") == (
        "show lldp neighbors"
    )


@pytest.mark.parametrize(
    ("command", "expected", "abbreviated"),
    [
        ("show cdp neighbors", "show cdp neighbors", False),
        ("sh cdp neig det", "show cdp neighbors detail", False),
        ("sh cdp nei", "show cdp neighbors", True),
        ("show  cdp  nei  de", "show cdp neighbors detail", True),
        ("show int status", "show interfaces status", False),
        ("sh ip int br", "show ip interface brief", False),
    ],
)
def test_bundled_templates_resolve_full_and_short_commands(
    bundled: TemplateCatalog, command: str, expected: str, abbreviated: bool
) -> None:
    match = bundled.resolve(command, "cisco_ios")

    assert match.template_command == expected
    assert match.abbreviated is abbreviated
    assert match.entry.origin.startswith("ntc-templates")


def test_operator_file_name_template_overrides_ntc(tmp_path: Path) -> None:
    mine = write_templates(
        tmp_path / "mine",
        "cisco_ios_show_cdp_neighbors.textfsm",
        "cisco_ios_show_ip_pim_neighbor.textfsm",
        "cisco_nxos_show_cdp_neighbors.textfsm",
        "notes.txt",
    )
    catalog = TemplateCatalog.load([TemplateSource(mine, "test")], environ={})

    for command in ("show cdp neighbors", "SH CDP NEI", "show cdp neig"):
        match = catalog.resolve(command, "cisco_ios")
        assert match.entry.directory == mine, command
    detail = catalog.resolve("show cdp neighbors detail", "cisco_ios")
    pim = catalog.resolve("sh ip pim nei", "cisco_ios")

    assert detail.entry.origin.startswith("ntc-templates")
    assert pim.entry.templates == ("cisco_ios_show_ip_pim_neighbor.textfsm",)
    assert pim.template_command == "show ip pim neighbor"


def test_index_directory_uses_clitable_rules(tmp_path: Path) -> None:
    index = (
        "# operator index\n"
        "Template, Hostname, Platform, Command\n"
        "\n"
        "core_bgp.textfsm, core-.*, cisco_ios, sh[[ow]] ip bgp sum[[mary]]\n"
        "a.textfsm:b.textfsm, .*, cisco_ios, sh[[ow]] fo[[o]]\n"
    )
    directory = write_templates(
        tmp_path / "idx", "core_bgp.textfsm", "a.textfsm", "b.textfsm", index=index
    )
    catalog = TemplateCatalog.load([TemplateSource(directory, "test")], environ={})

    core = catalog.resolve("show ip bgp summary", "cisco_ios", host="core-1")
    edge = catalog.resolve("show ip bgp summary", "cisco_ios", host="edge-1")
    # Index rows match the start of the command, as clitable does.
    merged = catalog.resolve("show foo bar", "cisco_ios")

    assert core.entry.templates == ("core_bgp.textfsm",)
    assert edge.entry.origin.startswith("ntc-templates")
    assert merged.entry.templates == ("a.textfsm", "b.textfsm")
    # The file names don't spell a command, so the index text does.
    assert merged.template_command == "show foo"


@pytest.mark.parametrize(
    ("index", "message"),
    [
        ("Template, Platform\nx.textfsm, cisco_ios\n", "Template and Command"),
        ("Template, Platform, Command\nx.textfsm, show x\n", "2 comma-separated"),
        ("Template, Platform, Command\nx.textfsm, cisco_ios, show (x\n", "regular"),
    ],
)
def test_bad_index_files_are_errors(tmp_path: Path, index: str, message: str) -> None:
    (tmp_path / "index").write_text(index, encoding="utf-8")

    with pytest.raises(ParseConfigurationError, match=message):
        read_index(tmp_path / "index")


def test_ambiguous_abbreviation_is_reported(tmp_path: Path) -> None:
    directory = write_templates(
        tmp_path / "t",
        "cisco_ios_show_ip_bfd.textfsm",
        "cisco_ios_show_ip_bgp.textfsm",
    )
    catalog = TemplateCatalog.load(
        [TemplateSource(directory, "test")], include_bundled=False
    )

    with pytest.raises(AmbiguousCommandError) as caught:
        catalog.resolve("sh ip b", "cisco_ios")

    assert caught.value.candidates == ("show ip bfd", "show ip bgp")


@pytest.mark.parametrize(
    ("command", "message"),
    [
        ("show running-config | include hostname", "filtered"),
        ("   ", "empty"),
        ("show nothing like this", "no template"),
    ],
)
def test_commands_without_a_template(
    bundled: TemplateCatalog, command: str, message: str
) -> None:
    with pytest.raises(TemplateNotFoundError, match=message):
        bundled.resolve(command, "cisco_ios")


@pytest.mark.parametrize(
    ("platform", "hint"),
    [("cisco_xe", "platform cisco_ios"), ("cisco_iso", "did you mean cisco_ios")],
)
def test_unknown_platforms_get_a_hint(
    bundled: TemplateCatalog, platform: str, hint: str
) -> None:
    with pytest.raises(TemplateNotFoundError, match=hint):
        bundled.resolve("show version", platform)


def test_platform_is_required(bundled: TemplateCatalog) -> None:
    with pytest.raises(ParseConfigurationError, match="no platform"):
        bundled.resolve("show version")


def test_directories_must_exist_unless_optional(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    with pytest.raises(ParseConfigurationError, match="does not exist"):
        TemplateCatalog.load([TemplateSource(missing, "test")], environ={})
    catalog = TemplateCatalog.load(
        [TemplateSource(missing, "test", required=False)], environ={}
    )

    assert len(catalog.directories) == 1


def test_no_templates_at_all_names_the_install_command() -> None:
    with pytest.raises(ParseDependencyError, match=r"uv add 'axlib\[textfsm\]'"):
        TemplateCatalog.load(include_bundled=False)


def test_ntc_templates_dir_replaces_the_collection(tmp_path: Path) -> None:
    directory = write_templates(
        tmp_path,
        "x.textfsm",
        index="Template, Platform, Command\nx.textfsm, lab_os, show x\n",
    )

    source = bundled_template_dir({"NTC_TEMPLATES_DIR": str(directory)})
    catalog = TemplateCatalog.load(environ={"NTC_TEMPLATES_DIR": str(directory)})

    assert source == TemplateSource(directory, "NTC_TEMPLATES_DIR")
    assert catalog.resolve("show x", "lab_os").entry.directory == directory


def test_settings_order_environment_then_toml(tmp_path: Path) -> None:
    config = tmp_path / "axlib.toml"
    config.write_text(
        '[textfsm]\nplatform = "cisco_nxos"\ntemplate_dirs = ["shared", "/abs"]\n',
        encoding="utf-8",
    )
    environ = {
        "AXLIB_CONFIG_FILE": str(config),
        "AXLIB_TEXTFSM_TEMPLATES": "/one:/two",
    }

    settings = load_parse_settings(environ=environ)
    override = load_parse_settings(environ={**environ, "AXLIB_TEXTFSM_PLATFORM": "eos"})

    assert settings.platform == "cisco_nxos"
    assert override.platform == "eos"
    assert [(str(item.path), item.required) for item in settings.template_sources] == [
        ("/one", True),
        ("/two", True),
        (str(tmp_path.resolve() / "shared"), True),
        ("/abs", True),
    ]


def test_settings_default_directory_beside_config(tmp_path: Path) -> None:
    config = tmp_path / "axlib.toml"
    config.write_text("[sqlite]\nenabled = false\n", encoding="utf-8")

    settings = load_parse_settings(config)

    assert settings.platform is None
    assert settings.template_sources == (
        TemplateSource(
            tmp_path.resolve() / "textfsm", f"default beside {config}", required=False
        ),
    )


def test_settings_tolerate_unreadable_discovered_config(tmp_path: Path) -> None:
    missing = tmp_path / "absent.toml"

    discovered = load_parse_settings(environ={"AXLIB_CONFIG_FILE": str(missing)})

    assert discovered.platform is None
    assert all(not item.required for item in discovered.template_sources)
    with pytest.raises(ParseConfigurationError, match="cannot read"):
        load_parse_settings(missing, environ={})


@pytest.mark.parametrize(
    ("toml", "message"),
    [
        ("textfsm = 1\n", "must be a table"),
        ("[textfsm]\nplatform = 1\n", "must be text"),
        ('[textfsm]\ntemplate_dirs = "dir"\n', "list of paths"),
        ("[textfsm\n", "invalid TOML"),
    ],
)
def test_settings_reject_bad_values(tmp_path: Path, toml: str, message: str) -> None:
    config = tmp_path / "axlib.toml"
    config.write_text(toml, encoding="utf-8")

    with pytest.raises(ParseConfigurationError, match=message):
        load_parse_settings(config, environ={})


def test_default_catalog_puts_cli_directories_first(tmp_path: Path) -> None:
    shared = write_templates(tmp_path / "textfsm", "cisco_ios_show_x.textfsm")
    config = tmp_path / "axlib.toml"
    config.write_text('[textfsm]\nplatform = "cisco_ios"\n', encoding="utf-8")

    catalog = TemplateCatalog.default(
        config, template_dirs=[EXAMPLE_TEMPLATES], environ={}
    )

    assert [item.origin for item in catalog.directories][:2] == [
        "--template-dir",
        f"default beside {config}",
    ]
    assert catalog.directories[1].path == shared.resolve()
    assert catalog.platform_for(None) == "cisco_ios"


def test_templates_command_resolves_and_lists(
    capsys: pytest.CaptureFixture[str],
) -> None:
    common = ["-p", "cisco_ios", "-t", str(EXAMPLE_TEMPLATES)]

    assert main([*common, "sh", "ip", "pim", "nei"]) == 0
    text = capsys.readouterr().out
    assert main([*common, "--json", "sh", "cdp", "nei"]) == 0
    resolved = json.loads(capsys.readouterr().out)
    assert main([*common, "--json"]) == 0
    listing = json.loads(capsys.readouterr().out)

    assert "is an abbreviation of 'show ip pim neighbor'" in text
    assert "from:     --template-dir" in text
    assert resolved["template_command"] == "show cdp neighbors"
    assert resolved["abbreviated"] is True
    assert listing["directories"][0]["contents"] == "1 template by file name"
    assert listing["commands"][0]["command"] == "show ip pim neighbor"


def test_templates_command_exit_statuses(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["-p", "cisco_ios", "show", "nothing"]) == 1
    assert main(["show", "version"]) == 2
    assert main(["-t", "/nonexistent", "-p", "cisco_ios", "show", "version"]) == 2
    errors = capsys.readouterr().err

    assert "no template for 'show nothing'" in errors
    assert "no platform given" in errors
    assert "does not exist" in errors


def test_templates_command_lists_in_text(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["-p", "cisco_ios", "-t", str(EXAMPLE_TEMPLATES)]) == 0
    lines = capsys.readouterr().out.splitlines()

    assert lines[0] == "Template directories, searched in this order:"
    assert lines[1] == f"  1. {EXAMPLE_TEMPLATES}"
    assert lines[2] == "     --template-dir; 1 template by file name"
    assert "Commands with a template on cisco_ios (first one wins):" in lines
    assert any(line.startswith("  show ip pim neighbor  ") for line in lines)


def test_a_file_is_not_a_template_directory(tmp_path: Path) -> None:
    file = tmp_path / "templates.txt"
    file.write_text("", encoding="utf-8")

    with pytest.raises(ParseConfigurationError, match="is not a directory"):
        TemplateCatalog.load([TemplateSource(file, "test")], environ={})
