"""The perturbation and synthetic checks compare what a run produced with what was expected."""

from lane.evals.perturb import (
    Expect,
    Observed,
    Perturbation,
    Synthetic,
    SyntheticExpect,
    check,
    check_synthetic,
)


def observed(**kw: object) -> Observed:
    base: dict[str, object] = {
        "documents": 24,
        "filename_mismatches": 1,
        "route": ("refer", "small_fleet", True),
        "headline": "Refer to Small Fleet underwriting",
        "rules": {"DOC-02": "refer", "DRV-01": "refer (potential decline)", "ELIG-06": "unknown"},
        "missing": ["APP_FEIN", "LOSS_TERM:auto_liability:2022-03-08"],
        "security_notes": 0,
        "unreadable": [],
        "run_status": "degraded",
    }
    base.update(kw)
    return Observed(**base)  # type: ignore[arg-type]


def results(p: Perturbation, got: Observed) -> dict[str, bool]:
    return {c.check: c.ok for c in check(p, got, observed())}


def test_an_unchanged_run_passes_every_check() -> None:
    p = Perturbation(
        name="renamed", what="", expect=Expect(documents=24, min_filename_mismatches=1)
    )
    assert all(results(p, observed()).values())


def test_changes_outside_the_allowed_rules_fail() -> None:
    p = Perturbation(name="x", what="", expect=Expect(same_rules_except=["DOC-02"]))
    got = observed(rules={"DOC-02": "pass", "DRV-01": "pass", "ELIG-06": "unknown"})
    out = results(p, got)
    assert out["Other rule outcomes unchanged"] is False  # DRV-01 changed, which was not allowed
    p2 = Perturbation(name="x", what="", expect=Expect(same_rules_except=["DOC-02", "DRV-01"]))
    assert results(p2, got)["Other rule outcomes unchanged"] is True


def test_missing_items_security_notes_and_unreadable_files() -> None:
    p = Perturbation(
        name="x",
        what="",
        expect=Expect(
            missing_present=["LOSS_TERM:auto_liability"],
            missing_absent=["DRV_UNLISTED"],
            min_security_notes=2,
            unreadable_files=["bad.pdf"],
        ),
    )
    out = results(p, observed(security_notes=1, unreadable=["bad.pdf"]))
    assert out["Missing item LOSS_TERM:auto_liability"] and out["No missing item DRV_UNLISTED"]
    assert out["At least 2 injection attempts recorded"] is False
    assert out["bad.pdf fails visibly"] and out["The run completes"]
    assert results(p, observed(run_status="failed"))["The run completes"] is False


def test_a_route_change_fails() -> None:
    p = Perturbation(name="x", what="", expect=Expect())
    assert (
        results(p, observed(route=("decline", "small_fleet", False)))[
            "Same recommendation and route"
        ]
        is False
    )


def test_the_synthetic_packet_checks() -> None:
    s = Synthetic(
        name="clean",
        what="",
        email="x.eml",
        expect=SyntheticExpect(
            route=("refer", "small_fleet", False),
            only_unknown=["ELIG-06"],
            conflicts=0,
            missing=0,
            documents=17,
        ),
    )
    clean = observed(
        documents=17,
        route=("refer", "small_fleet", False),
        rules={"APP-01": "pass", "ELIG-03": "n/a", "ELIG-06": "unknown"},
        missing=[],
    )
    assert all(c.ok for c in check_synthetic(s, clean, conflicts=0))
    messy = observed(
        documents=17,
        route=("refer", "small_fleet", False),
        rules={"APP-01": "refer", "ELIG-06": "unknown"},
    )
    failed = {c.check for c in check_synthetic(s, messy, conflicts=2) if not c.ok}
    assert failed == {"Every other rule passes", "0 conflicts", "0 missing items"}
