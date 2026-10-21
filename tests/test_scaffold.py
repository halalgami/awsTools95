import pytest

import aws95
from aws95.app import build_parser, main


def test_version_is_single_sourced():
    assert aws95.__version__ == "3.1.0"


def test_version_flag_prints_and_exits(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "AWS 95 Tools 3.1.0" in capsys.readouterr().out


def test_parser_knows_demo_and_gallery():
    args = build_parser().parse_args(["--demo", "--gallery"])
    assert args.demo and args.gallery
