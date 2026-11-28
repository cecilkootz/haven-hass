"""Constants for the Haven Lighting integration."""

DOMAIN = "haven_lighting"


def location_identifier(location_id: int) -> tuple[str, str]:
    """Device-registry identifier of a Haven location.

    Every Haven device identifier carries its kind ("location_", "light_",
    "zone_"): locations, lights and zones are numbered independently, so a
    bare number could name two different things.
    """
    return (DOMAIN, f"location_{location_id}")
