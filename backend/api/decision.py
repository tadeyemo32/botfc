"""Decision engine – port of HttpServer::computeDecision()."""


def compute_decision(telemetry: dict) -> str:
    """Mirror FSM logic: recommend an action from the latest telemetry."""
    if not telemetry.get("ball_valid", False):
        return "SEARCH"
    bsz = telemetry.get("ball_bsz", 0.0)
    bx = telemetry.get("ball_bx", 0.0)
    dist = telemetry.get("ball_dist", 99.0)
    if dist > 0.8 or bsz < 0.04:
        return "APPROACH"
    if abs(bx) > 0.12:
        return "ALIGN"
    return "KICK"
