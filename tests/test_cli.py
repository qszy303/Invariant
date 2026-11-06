from pathlib import Path

from proof import cli

CLEAN = Path(__file__).parent / "fixtures" / "clean" / "checkinput.json"


def test_check_without_checker_exits_cleanly(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_load_checker", lambda: None)
    assert cli.main(["check", str(CLEAN)]) == 2
    assert "not implemented" in capsys.readouterr().err


def test_check_prints_canonical_json(monkeypatch, capsys):
    from proof.schemas import CheckResult

    monkeypatch.setattr(
        cli, "_load_checker", lambda: lambda ci: CheckResult(ok=True, violations=[])
    )
    assert cli.main(["check", str(CLEAN)]) == 0
    assert capsys.readouterr().out.strip() == '{"ok":true,"violations":[]}'
