from fastapi import APIRouter, HTTPException, Query

from shared.clients.postgres import transaction
from apps.api.models import SocialPost

router = APIRouter(prefix="/posts", tags=["posts"])


@router.get("", response_model=list[SocialPost])
def list_posts(
    status: str | None = Query(None),
    platform: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List social posts, optionally filtered by status or platform."""
    filters = []
    params: list = []
    if status:
        filters.append(f"status = %s")
        params.append(status)
    if platform:
        filters.append("platform = %s")
        params.append(platform)

    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    params += [limit, offset]

    with transaction() as cur:
        cur.execute(
            f"""
            SELECT id, post_type, platform, status, puzzle_number, clue_ref,
                   parent_post_id, scheduled_for, attempt_count,
                   platform_post_id, platform_url, published_at, created_at
            FROM social_posts
            {where}
            ORDER BY scheduled_for DESC
            LIMIT %s OFFSET %s
            """,
            params,
        )
        rows = cur.fetchall()
    return [SocialPost.from_row(dict(r)) for r in rows]


@router.get("/{post_id}", response_model=SocialPost)
def get_post(post_id: int):
    """Get a single social post by ID."""
    with transaction() as cur:
        cur.execute(
            """
            SELECT id, post_type, platform, status, puzzle_number, clue_ref,
                   parent_post_id, scheduled_for, attempt_count,
                   platform_post_id, platform_url, published_at, created_at
            FROM social_posts WHERE id = %s
            """,
            (post_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Post not found")
    return SocialPost.from_row(dict(row))
