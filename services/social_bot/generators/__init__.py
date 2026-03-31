"""
ContentGenerator protocol.

Any generator must implement generate(post: PostRecord) -> RenderedContent.
Generators are stateless — all context is fetched from the DB inside generate().
"""

from typing import Protocol

from ..models import PostRecord, RenderedContent


class ContentGenerator(Protocol):
    def generate(self, post: PostRecord) -> RenderedContent:
        ...


# Generator registry: PostType string → ContentGenerator instance
# Populated explicitly in worker.py main() — no magic imports.
_registry: dict[str, "ContentGenerator"] = {}


def register(post_type: str, generator: "ContentGenerator") -> None:
    _registry[post_type] = generator


def get_generator(post_type: str) -> "ContentGenerator":
    if post_type not in _registry:
        raise ValueError(f"No generator registered for post type: {post_type!r}")
    return _registry[post_type]
