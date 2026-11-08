"""Variables rendered into shell commands must reach the program byte-for-byte."""
import subprocess

import pytest

from app.core.templating import template_engine

NASTY = [
    "P@ss$w0rd!",
    "a'b\"c",
    "x;id`whoami`$(id)",
    "back\\slash",
    "with space",
    "*",
    "",
]


def run_args(template: str, **vars) -> list:
    cmd = template_engine.render(template, vars, shell=True)
    out = subprocess.run(
        ["sh", "-c", f"set -- {cmd}; printf '%s\\0' \"$@\""],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.split("\0")[:-1]


@pytest.mark.parametrize("pw", NASTY)
@pytest.mark.parametrize("tpl", ["-p '{password}'", '-p "{password}"'])
def test_password_survives_quoted_template(tpl, pw):
    assert run_args(tpl, password=pw) == ["-p", pw]


@pytest.mark.parametrize("pw", [p for p in NASTY if " " not in p])
def test_password_survives_bare_template(pw):
    # Bare placeholder keeps sh word-splitting on whitespace (multi-target vars rely on it);
    # every other metacharacter is neutralised.
    assert run_args("-p {password}", password=pw) == ["-p", pw]


@pytest.mark.parametrize("pw", [p for p in NASTY if " " not in p])
def test_embedded_in_word(pw):
    assert run_args("{domain}/{username}:{password}@10.0.0.1", domain="CORP", username="bob", password=pw) == [
        f"CORP/bob:{pw}@10.0.0.1"
    ]


def test_multiword_and_list_still_split():
    assert run_args("{hosts}", hosts="10.0.0.1 10.0.0.2") == ["10.0.0.1", "10.0.0.2"]
    assert run_args("{hosts}", hosts=["10.0.0.1", "a b"]) == ["10.0.0.1", "a b"]


def test_plain_values_unchanged_and_default_is_raw():
    assert template_engine.render("nmap -p {p} {t}", {"p": "22,80", "t": "10.0.0.1"}, shell=True) == "nmap -p 22,80 10.0.0.1"
    assert template_engine.render("x {v}", {"v": "a;b"}) == "x a;b"  # flow conditions etc. stay raw
