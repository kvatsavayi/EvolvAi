from core.router.router import Router


def test_router_signal_pressure_updates_weights() -> None:
    router = Router(["pod_a"])
    base = router.weights["pod_a"]

    up = router.apply_signal_pressure(
        pod_id="pod_a",
        request_type="general",
        completion_rate=1.0,
        retry_rate=0.0,
        return_rate=1.0,
        time_to_resolution_ms=500.0,
    )
    assert up > base

    down = router.apply_signal_pressure(
        pod_id="pod_a",
        request_type="general",
        completion_rate=0.0,
        retry_rate=1.0,
        return_rate=0.0,
        time_to_resolution_ms=9000.0,
    )
    assert down < up
