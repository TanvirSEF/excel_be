from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import PermissionDeniedException, ValidationException
from app.models import RolePermission, User, UserRole
from app.services import audit_service

PERMISSION_GROUPS = [
    {
        "id": "content",
        "title": "Posts & Editorial",
        "description": "Article authoring, reviewing, scheduling, and deletion capabilities.",
        "permissions": [
            {
                "id": "posts:view",
                "name": "View Posts",
                "description": "View all articles, drafts, and editorial revisions.",
            },
            {
                "id": "posts:manage",
                "name": "Manage Posts",
                "description": "Create and edit articles, headings, formulas, and content blocks.",
            },
            {
                "id": "posts:publish",
                "name": "Publish Posts",
                "description": "Publish drafts, schedule release dates, or unpublish live posts.",
            },
            {
                "id": "posts:delete",
                "name": "Delete Posts",
                "description": "Move articles to trash or permanently remove posts.",
            },
        ],
    },
    {
        "id": "seo",
        "title": "SEO & Metadata",
        "description": "Search engine optimization and structured schema markup.",
        "permissions": [
            {
                "id": "seo:edit",
                "name": "Edit SEO Metadata",
                "description": "Edit meta titles, descriptions, canonical URLs, and schema markup types.",
            },
        ],
    },
    {
        "id": "taxonomy",
        "title": "Taxonomy & Organization",
        "description": "Management of article categories, topics, and taxonomy tags.",
        "permissions": [
            {
                "id": "categories:manage",
                "name": "Manage Categories",
                "description": "Create, edit, sort, and organize article categories.",
            },
            {
                "id": "tags:view",
                "name": "View Tags",
                "description": "Browse and search the global tags directory.",
            },
            {
                "id": "tags:create",
                "name": "Create Tags",
                "description": "Create new tags during post creation or tag editing.",
            },
            {
                "id": "tags:manage",
                "name": "Manage Tags",
                "description": "Edit, merge, cleanup, and delete existing tags.",
            },
        ],
    },
    {
        "id": "media",
        "title": "Media & Assets",
        "description": "Image gallery, spreadsheets, and file asset administration.",
        "permissions": [
            {
                "id": "media:view",
                "name": "View Media Library",
                "description": "Browse uploaded screenshots, illustrations, and assets.",
            },
            {
                "id": "media:upload",
                "name": "Upload Media",
                "description": "Upload new image files, spreadsheets, and assets to cloud storage.",
            },
            {
                "id": "media:manage",
                "name": "Manage Media",
                "description": "Edit image alt text, replace assets, and delete media files.",
            },
        ],
    },
    {
        "id": "community",
        "title": "Community & Moderation",
        "description": "Reader feedback, user discussions, and comment moderation.",
        "permissions": [
            {
                "id": "comments:moderate",
                "name": "Moderate Comments",
                "description": "Approve, reject, flag as spam, or delete reader comments.",
            },
        ],
    },
    {
        "id": "analytics",
        "title": "Insights & Analytics",
        "description": "Website traffic, search impressions, and editorial metrics.",
        "permissions": [
            {
                "id": "overview:view",
                "name": "View Overview",
                "description": "Access dashboard overview cards and performance summaries.",
            },
            {
                "id": "analytics:view",
                "name": "View Analytics",
                "description": "Inspect Google Analytics traffic, referral sources, and post views.",
            },
        ],
    },
    {
        "id": "administration",
        "title": "System & Administration",
        "description": "High-level access control, user accounts, and audit logging.",
        "permissions": [
            {
                "id": "users:manage",
                "name": "Manage Users",
                "description": "Invite, edit, reassign roles, and deactivate team accounts.",
            },
            {
                "id": "audit:view",
                "name": "View Audit Logs",
                "description": "Inspect security audit trails and administrative change logs.",
            },
            {
                "id": "settings:view",
                "name": "View Settings",
                "description": "Access settings and personal profile preferences.",
            },
            {
                "id": "settings:manage",
                "name": "Manage Settings",
                "description": "Configure system-wide settings, WordPress imports, and site backups.",
            },
        ],
    },
]

ALL_KNOWN_PERMISSIONS = {
    perm["id"]
    for group in PERMISSION_GROUPS
    for perm in group["permissions"]
}

DEFAULT_ROLE_PERMISSIONS: dict[UserRole, list[str]] = {
    UserRole.super_admin: ["*"],
    UserRole.senior_editor: [
        "overview:view",
        "posts:view",
        "posts:manage",
        "posts:delete",
        "posts:publish",
        "seo:edit",
        "comments:moderate",
        "media:view",
        "media:manage",
        "categories:manage",
        "tags:view",
        "tags:manage",
        "analytics:view",
        "settings:view",
    ],
    UserRole.technical_writer: [
        "overview:view",
        "posts:view",
        "posts:manage",
        "media:view",
        "media:upload",
        "tags:view",
        "tags:create",
        "settings:view",
    ],
    UserRole.seo_specialist: [
        "overview:view",
        "seo:edit",
        "analytics:view",
        "settings:view",
    ],
}

ROLE_METADATA: dict[UserRole, dict[str, str]] = {
    UserRole.super_admin: {
        "name": "Super Admin",
        "description": "Full administrative control over all system modules, team members, role permissions, audit trails, and platform configurations.",
    },
    UserRole.senior_editor: {
        "name": "Senior Editor",
        "description": "Editorial authority to author, review, schedule, publish, or remove posts, assign authors, manage SEO metadata, and moderate comments.",
    },
    UserRole.technical_writer: {
        "name": "Technical Writer",
        "description": "Content creator focused on authoring, drafting, and updating assigned technical spreadsheet tutorials and uploading media assets.",
    },
    UserRole.seo_specialist: {
        "name": "SEO Specialist",
        "description": "Search strategist responsible for metadata optimization, focus keyphrases, structured schema markup, and performance analytics.",
    },
}


async def get_permission_groups() -> list[dict]:
    return PERMISSION_GROUPS


async def get_role_permissions(db: AsyncSession, role: UserRole) -> list[str]:
    if role == UserRole.super_admin:
        return ["*"]

    try:
        results = await db.scalars(
            select(RolePermission.permission)
            .where(RolePermission.role == role)
            .order_by(RolePermission.permission.asc())
        )
        custom_perms = results.all()
        if custom_perms:
            return list(custom_perms)
    except Exception:
        # Fallback to default if table is not yet migrated
        pass

    return DEFAULT_ROLE_PERMISSIONS.get(role, [])


async def list_roles(db: AsyncSession) -> list[dict]:
    # Query active member counts per role
    counts_query = (
        select(User.role, func.count())
        .where(User.is_active.is_(True))
        .group_by(User.role)
    )
    counts_result = await db.execute(counts_query)
    member_counts = {role: count for role, count in counts_result.all()}

    roles_list = []
    for role_enum in UserRole:
        meta = ROLE_METADATA[role_enum]
        perms = await get_role_permissions(db, role_enum)
        roles_list.append({
            "role": role_enum,
            "name": meta["name"],
            "description": meta["description"],
            "member_count": member_counts.get(role_enum, 0),
            "is_system": True,
            "is_editable": role_enum != UserRole.super_admin,
            "permissions": perms,
        })

    return roles_list


async def update_role_permissions(
    db: AsyncSession,
    current_user: User,
    role: UserRole,
    new_permissions: list[str],
) -> dict:
    if current_user.role != UserRole.super_admin:
        raise PermissionDeniedException("Only super admins can update role permissions")

    if role == UserRole.super_admin:
        raise ValidationException(
            "Super Admin permissions cannot be modified (wildcard root access required)",
            code="SUPER_ADMIN_IMMUTABLE",
        )

    # Filter/validate permissions
    sanitized_perms = sorted(
        {p for p in new_permissions if p in ALL_KNOWN_PERMISSIONS}
    )

    # Fetch previous permissions for audit logging
    previous_perms = await get_role_permissions(db, role)

    # Delete existing permissions for role
    await db.execute(
        delete(RolePermission).where(RolePermission.role == role)
    )

    # Insert new permissions
    for perm in sanitized_perms:
        db.add(RolePermission(role=role, permission=perm))

    audit_service.record(
        db,
        current_user.id,
        "role.permissions_update",
        "role",
        None,
        {
            "role": role.value,
            "before": previous_perms,
            "after": sanitized_perms,
        },
    )

    await db.commit()

    meta = ROLE_METADATA[role]
    # Get member count
    count = await db.scalar(
        select(func.count())
        .select_from(User)
        .where(User.role == role, User.is_active.is_(True))
    )

    return {
        "role": role,
        "name": meta["name"],
        "description": meta["description"],
        "member_count": count or 0,
        "is_system": True,
        "is_editable": True,
        "permissions": sanitized_perms,
    }
