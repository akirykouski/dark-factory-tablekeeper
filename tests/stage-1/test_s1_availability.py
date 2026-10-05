"""S1-44..S1-49, S1-63..S1-66: availability, occupancy and DST."""
import pytest

from conftest import FRI, SAT, THU, THU_WINTER, TS_RE, assert_error, fresh_fixture


def times(body):
    return [s["starts_at_local"][11:] for s in body["slots"]]


def test_shape_and_grid(api):
    r = api.avail(party_size=2)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"restaurant_id", "date", "timezone", "slots"}
    assert body["restaurant_id"] == "r_anker"
    assert body["date"] == THU
    assert body["timezone"] == "Europe/Berlin"
    assert times(body) == ["18:00", "18:30", "19:00", "19:30", "20:00", "20:30", "21:00",
                           "21:30"]
    for s in body["slots"]:
        assert set(s) == {"starts_at_local", "starts_at", "available_table_ids"}
        assert TS_RE.match(s["starts_at"])
        assert s["starts_at"] == s["starts_at_local"] + ":00+02:00"
        assert s["available_table_ids"] == ["t_1", "t_2", "t_3"]


def test_last_slot_respects_close(api):
    assert times(api.avail(date=FRI).json())[-1] == "22:00"


def test_winter_offset(api):
    s = api.avail(date=THU_WINTER).json()["slots"][0]
    assert s["starts_at"] == f"{THU_WINTER}T18:00:00+01:00"


def test_other_grid(api):
    body = api.avail("r_hudson", "2030-09-30", 1).json()
    assert body["timezone"] == "America/New_York"
    t = times(body)
    assert t[0] == "17:00" and t[1] == "17:15" and t[-1] == "20:30" and len(t) == 15
    assert body["slots"][0]["starts_at"] == "2030-09-30T17:00:00-04:00"


def test_capacity_filter(api):
    s = api.avail(party_size=4).json()["slots"][0]
    assert s["available_table_ids"] == ["t_2", "t_3"]
    s = api.avail(party_size=6).json()["slots"][0]
    assert s["available_table_ids"] == ["t_3"]


def test_party_larger_than_every_table(api):
    body = api.avail(party_size=7).json()
    assert len(body["slots"]) == 8
    assert all(s["available_table_ids"] == [] for s in body["slots"])


def test_closed_day(api):
    body = api.avail(date=SAT).json()
    assert body["slots"] == []
    assert body["date"] == SAT


def test_overlap_half_open(api, ada):
    api.book(ada, table_id="t_2", local=f"{THU}T19:00")
    by = {s["starts_at_local"][11:]: s["available_table_ids"] for s in api.avail().json()["slots"]}
    assert by["18:00"] == ["t_1", "t_3"]       # 18:00-19:30 overlaps 19:00
    assert by["18:30"] == ["t_1", "t_3"]
    assert by["19:00"] == ["t_1", "t_3"]
    assert by["20:00"] == ["t_1", "t_3"]
    assert by["17:30" if False else "20:30"] == ["t_1", "t_2", "t_3"]  # starts at end
    # a slot of empty availability still appears
    api.book(ada, table_id="t_1", local=f"{THU}T19:00")
    api.book(ada, table_id="t_3", local=f"{THU}T19:00")
    by = {s["starts_at_local"][11:]: s["available_table_ids"] for s in api.avail().json()["slots"]}
    assert by["19:00"] == []
    assert len(by) == 8


def test_booking_adjacent_succeeds(api, ada):
    api.book(ada, table_id="t_2", local=f"{THU}T19:00")
    api.book(ada, table_id="t_2", local=f"{THU}T20:30")
    api.book(ada, table_id="t_2", local=f"{THU}T17:30" if False else f"{THU}T18:00",
             expect=409)


def test_cancel_frees_slot(api, ada):
    ref = api.book(ada, table_id="t_2", local=f"{THU}T19:00").json()["reference"]
    assert "t_2" not in api.slot("r_anker", THU, 2, f"{THU}T19:00")["available_table_ids"]
    assert api.post(f"/reservations/{ref}/cancel", {}, token=ada).status_code == 200
    assert "t_2" in api.slot("r_anker", THU, 2, f"{THU}T19:00")["available_table_ids"]


def test_other_restaurant_not_affected(api, ada):
    api.book(ada, table_id="t_2", local=f"{THU}T19:00")
    s = api.slot("r_hudson", "2030-09-26", 2, "2030-09-26T19:00")
    assert s["available_table_ids"] == ["h_1", "h_2"]


@pytest.mark.parametrize("missing", ["restaurant_id", "date", "party_size"])
def test_missing_param(api, missing):
    params = {"restaurant_id": "r_anker", "date": THU, "party_size": "2"}
    del params[missing]
    assert_error(api.get("/availability", params=params), 422, "validation_failed")


@pytest.mark.parametrize("ps", ["1e9", "4.0", "+4", "-1", "0", " 4", "4 ", "", "abc", "0x4",
                                "true", "4\n"])
def test_bad_party_size_query(api, ps):
    assert_error(api.avail(party_size=ps), 422, "validation_failed")


@pytest.mark.parametrize("d", ["2030-02-30", "2030-13-01", "2030-9-26", "20300926",
                               "2030-09-26T00:00", " 2030-09-26", "2030-09-26 ", "", "tomorrow",
                               "2030-09-26\n", "2030-02-29"])
def test_bad_date(api, d):
    assert_error(api.avail(date=d), 422, "validation_failed")


def test_leap_day_valid(api):
    assert api.avail(date="2028-02-29").status_code == 200


def test_unknown_restaurant(api):
    assert_error(api.avail(restaurant_id="nope"), 404, "not_found")


def test_validation_before_not_found(api):
    assert_error(api.avail(restaurant_id="nope", party_size="0"), 422, "validation_failed")


def test_unknown_query_params_ignored(api):
    r = api.avail(foo="bar")
    assert r.status_code == 200


def test_party_size_leading_zero_digits(api):
    # plain decimal digits; value 4
    r = api.avail(party_size="04")
    assert r.status_code == 200
    assert r.json()["slots"][0]["available_table_ids"] == ["t_2", "t_3"]


# --- DST ------------------------------------------------------------------------

def test_berlin_spring_forward(api):
    body = api.avail(date="2026-03-29").json()
    t = times(body)
    assert "02:00" not in t and "02:30" not in t
    assert t[:4] == ["00:00", "00:30", "01:00", "01:30"]
    assert "03:00" in t
    by = {s["starts_at_local"][11:]: s["starts_at"] for s in body["slots"]}
    assert by["01:30"] == "2026-03-29T01:30:00+01:00"
    assert by["03:00"] == "2026-03-29T03:00:00+02:00"
    assert t[-1] == "04:30"


def test_berlin_fall_back(api):
    body = api.avail(date="2026-10-25").json()
    t = times(body)
    assert t.count("02:00") == 1 and t.count("02:30") == 1
    by = {s["starts_at_local"][11:]: s["starts_at"] for s in body["slots"]}
    assert by["02:00"] == "2026-10-25T02:00:00+02:00"
    assert by["02:30"] == "2026-10-25T02:30:00+02:00"
    assert by["03:00"] == "2026-10-25T03:00:00+01:00"
    assert t[-1] == "04:30"


def test_new_york_transitions(api):
    t = times(api.avail("r_hudson", "2026-03-08", 1).json())
    assert not any(x.startswith("02:") for x in t)
    body = api.avail("r_hudson", "2026-11-01", 1).json()
    by = {}
    for s in body["slots"]:
        assert s["starts_at_local"] not in by
        by[s["starts_at_local"]] = s["starts_at"]
    assert by["2026-11-01T01:30"] == "2026-11-01T01:30:00-04:00"
    assert by["2026-11-01T02:00"] == "2026-11-01T02:00:00-05:00"


def test_spring_forward_booking_invalid_local_time(api, ada):
    r = api.book(ada, table_id="t_2", local="2026-03-29T02:30", expect=None)
    assert_error(r, 422, "invalid_local_time")
    r = api.book(ada, restaurant_id="r_hudson", table_id="h_1", local="2026-03-08T02:15",
                 expect=None)
    assert_error(r, 422, "invalid_local_time")


def test_fall_back_booking_absolute_duration(api, ada):
    b = api.book(ada, table_id="t_2", local="2026-10-25T01:30").json()
    assert b["starts_at"] == "2026-10-25T01:30:00+02:00"
    assert b["ends_at"] == "2026-10-25T02:00:00+01:00"
    b = api.book(ada, table_id="t_1", local="2026-10-25T02:30").json()
    assert b["starts_at"] == "2026-10-25T02:30:00+02:00"
    assert b["ends_at"] == "2026-10-25T03:00:00+01:00"
    b = api.book(ada, restaurant_id="r_hudson", table_id="h_1", local="2026-11-01T01:30").json()
    assert b["starts_at"] == "2026-11-01T01:30:00-04:00"
    assert b["ends_at"] == "2026-11-01T02:00:00-05:00"


def test_fall_back_occupancy_in_absolute_time(api, ada):
    # 01:30+02 .. 01:00Z ; slot 02:00 (+01:00 second occurrence not offered) – 02:30+02 is
    # 00:30Z which overlaps; 03:00+01 = 02:00Z does not.
    api.book(ada, table_id="t_2", local="2026-10-25T01:30")
    by = {s["starts_at_local"][11:]: s["available_table_ids"]
          for s in api.avail(date="2026-10-25").json()["slots"]}
    assert "t_2" not in by["02:30"]
    assert "t_2" in by["03:00"]


def test_spring_forward_duration(api, ada):
    b = api.book(ada, table_id="t_2", local="2026-03-29T01:30").json()
    assert b["ends_at"] == "2026-03-29T04:00:00+02:00"
