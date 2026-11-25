import pytest

from draupnir.cli import build_parser


def test_command_required():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_serve_defaults():
    args = build_parser().parse_args(["serve"])
    assert args.port == 5000 and not args.debug
