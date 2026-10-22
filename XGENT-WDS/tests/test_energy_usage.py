from __future__ import annotations

from energy_usage import parse_srum_energy_csv


def test_srum_energy_csv_aggregates_only_local_app_energy_and_drops_paths():
    result = parse_srum_energy_csv(
        "AppId,CPU Energy (mJ),Network Energy (mJ),MeasuredPower\n"
        'C:\\Program Files\\Browser\\browser.exe,120,30,False\n'
        'C:\\Program Files\\Browser\\browser.exe,40,10,False\n'
        'Microsoft.Store_1.0!App,90,0,True\n'
    )

    assert result == {
        "state": "supported",
        "source": "windows_srum_energy_estimation",
        "unit": "mJ",
        "apps": [
            {"name": "browser", "energy_mj": 200, "quality": "estimated"},
            {"name": "Microsoft.Store_1.0!App", "energy_mj": 90, "quality": "estimated"},
        ],
    }


def test_srum_energy_csv_uses_total_measure_once_and_converts_mwh():
    result = parse_srum_energy_csv(
        "Application Name,Total Energy (mWh),CPU Energy (mJ)\n"
        "Editor,2,900\n"
    )

    assert result == {
        "state": "supported",
        "source": "windows_srum_energy_estimation",
        "unit": "mJ",
        "apps": [{"name": "Editor", "energy_mj": 7200, "quality": "estimated"}],
    }


def test_srum_energy_csv_fails_closed_when_multiple_total_columns_are_ambiguous():
    result = parse_srum_energy_csv(
        "AppId,Total Energy (mJ),Overall Energy (mJ),CPU Energy (mJ)\n"
        "C:\\Apps\\browser.exe,100,200,50\n"
    )

    assert result == {
        "state": "unknown", "source": "windows_srum_energy_estimation", "apps": [],
    }


def test_srum_energy_csv_fails_closed_for_unknown_units_or_schema():
    assert parse_srum_energy_csv(
        "AppId,CPU Energy\neditor.exe,120\n"
    ) == {
        "state": "unknown", "source": "windows_srum_energy_estimation", "apps": [],
    }
    assert parse_srum_energy_csv("not,a,valid,srum,export\n") == {
        "state": "unknown", "source": "windows_srum_energy_estimation", "apps": [],
    }


def test_srum_energy_csv_rejects_invalid_values_and_bounds_top_apps():
    rows = ["AppId,Total Energy (mJ)"]
    rows.extend(f"app-{index}.exe,{index}" for index in range(1, 12))
    rows.extend(["bad.exe,-2", "nan.exe,NaN", "huge.exe,1000000000000001"])

    result = parse_srum_energy_csv("\n".join(rows))

    assert result["state"] == "supported"
    assert len(result["apps"]) == 8
    assert result["apps"][0] == {"name": "app-11", "energy_mj": 11, "quality": "estimated"}
    assert all("huge" not in row["name"] and "bad" not in row["name"] for row in result["apps"])


def test_srum_energy_csv_does_not_echo_raw_paths_or_identifiers():
    result = parse_srum_energy_csv(
        "AppId,Total Energy (mJ)\n"
        "C:\\Users\\Alice\\AppData\\Local\\private.exe,7\n"
    )

    assert result["apps"][0]["name"] == "private"
    assert "Alice" not in repr(result)
    assert "AppData" not in repr(result)
