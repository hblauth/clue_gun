"""
Publisher protocol and registry.

Any publisher must implement publish(post, content) -> PublishResult.
Publishers are responsible for their own auth, rate-limiting, and retries
at the API level. The worker handles retry scheduling at the job level.
"""

from typing import Protocol

from ..models import PostRecord, RenderedContent, PublishResult


class Publisher(Protocol):
    def publish(self, post: PostRecord, content: RenderedContent) -> PublishResult:
        ...


# Publisher registry: platform string → Publisher instance
# Populated explicitly in worker.py main() — no magic imports.
_registry: dict[str, "Publisher"] = {}


def register(platform: str, publisher: "Publisher") -> None:
    _registry[platform] = publisher


def get_publisher(platform: str) -> "Publisher":
    if platform not in _registry:
        raise ValueError(f"No publisher registered for platform: {platform!r}")
    return _registry[platform]
