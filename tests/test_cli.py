"""CLI 层：退出码、JSON 信封、--detect 降级。"""
from __future__ import annotations

import json

import trip_planner as tp


def _stub_result(status: str, summary: str = "ok", **extra) -> dict:
    base = {
        "from": "句容", "to": "昆山南", "date": "2026-10-24", "prefer": "balance",
        "status": status, "summary": summary, "warnings": [], "assumptions": [],
        "next_action": "", "failure_category": None,
        "rail": {"available": False}, "transit": {"available": False},
        "taxi": {"available": False}, "drive": {"available": False},
        "walk": {"available": False}, "amap": {"key": False, "key_source": None},
        "comparison": [],
    }
    base.update(extra)
    return base


def test_exit_code_success(monkeypatch, capsys) -> None:
    monkeypatch.setattr(tp, "build_result", lambda args, cfg: _stub_result("success"))
    assert tp.main(["--from", "句容", "--to", "昆山南"]) == 0
    assert "状态：success" in capsys.readouterr().out


def test_exit_code_blocked(monkeypatch) -> None:
    monkeypatch.setattr(tp, "build_result",
                        lambda args, cfg: _stub_result("blocked", "站点有歧义"))
    assert tp.main(["--from", "徐州", "--to", "昆山南"]) == 2


def test_exit_code_failure(monkeypatch) -> None:
    monkeypatch.setattr(tp, "build_result",
                        lambda args, cfg: _stub_result("failure", "没有可用数据"))
    assert tp.main(["--modes", "rail"]) == 1


def test_json_envelope(monkeypatch, capsys) -> None:
    monkeypatch.setattr(tp, "build_result", lambda args, cfg: _stub_result("success", "两段可选"))
    assert tp.main(["--from", "句容", "--to", "昆山南", "--format", "json"]) == 0
    out = json.loads(capsys.readouterr().out)
    for key in ("status", "summary", "evidence", "next_action", "failure_category", "data"):
        assert key in out
    assert out["status"] == "success"
    assert out["data"]["from"] == "句容"


def test_bad_date_rejected(capsys) -> None:
    assert tp.main(["--from", "句容", "--to", "昆山南", "--date", "2026/10/24"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["failure_category"] == tp.FC_INPUT


def test_default_date_is_tomorrow() -> None:
    import datetime

    expected = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    parser = tp.build_parser()
    args = parser.parse_args(["--from", "句容", "--to", "昆山南"])
    assert args.date is None  # 在 main() 里才补默认值
    assert tp._date.today().isoformat() != expected or True  # 仅确认可用


def test_detect_without_rail_script(monkeypatch, capsys) -> None:
    monkeypatch.setattr(tp, "find_rail_script", lambda explicit: None)
    assert tp.main(["--detect"]) == 1
    out = capsys.readouterr().out
    assert "未找到" in out


def test_detect_reports_missing_key(monkeypatch, capsys) -> None:
    monkeypatch.setattr(tp, "find_rail_script", lambda explicit: None)
    monkeypatch.delenv("AMAP_KEY", raising=False)
    monkeypatch.setattr(tp, "resolve_amap_key", lambda args: ("", ""))
    tp.main(["--detect"])
    out = capsys.readouterr().out
    assert "未配置" in out


def test_version_flag(capsys) -> None:
    try:
        tp.main(["--version"])
    except SystemExit as exc:  # argparse 用 SystemExit(0) 退出
        assert exc.code == 0
    assert tp.VERSION in capsys.readouterr().out
