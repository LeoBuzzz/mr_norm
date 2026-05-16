from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProfileConfig:
    name: str
    default_limit: int
    use_point: bool
    use_payload: bool
    use_vector: bool
    use_hybrid: bool
    vector_requires_query: bool = True


PROFILE_CONFIGS: dict[str, ProfileConfig] = {
    "fast": ProfileConfig(
        name="fast",
        default_limit=5,
        use_point=True,
        use_payload=True,
        use_vector=True,
        use_hybrid=False,
        vector_requires_query=True,
    ),
    "balanced": ProfileConfig(
        name="balanced",
        default_limit=10,
        use_point=True,
        use_payload=True,
        use_vector=True,
        use_hybrid=True,
        vector_requires_query=True,
    ),
    "deep": ProfileConfig(
        name="deep",
        default_limit=20,
        use_point=True,
        use_payload=True,
        use_vector=True,
        use_hybrid=True,
        vector_requires_query=True,
    ),
}


def get_profile_config(profile: str) -> ProfileConfig:
    return PROFILE_CONFIGS.get(profile, PROFILE_CONFIGS["balanced"])
