"""
Who is acting, and in whose hub. An owner works in their own hub; a team member (Growth plan and up)
works in the owner's hub with a role. Data is always scoped to the workspace (`ctx.ws` = the owner's id),
and plan features always come from the owner.
"""
from dataclasses import dataclass

from fastapi import Depends, HTTPException

from . import plans
from .db import db
from .security import current_user

ROLES = ("staff", "manager", "owner")


@dataclass
class Ctx:
    user: dict      # the person making the request
    owner: dict     # the business owner whose hub this is (the same person for owners)
    role: str       # owner | manager | staff

    @property
    def ws(self) -> str:
        return self.owner["_id"]

    @property
    def actor(self) -> str:
        return self.user["_id"]

    def has(self, feature: str) -> bool:
        return plans.has(self.owner, feature)

    def require(self, feature: str) -> None:
        if not self.has(feature):
            raise HTTPException(403, locked_detail(feature))

    def at_least(self, role: str) -> bool:
        return ROLES.index(self.role) >= ROLES.index(role)


def locked_detail(feature: str) -> dict:
    m = plans.META.get(feature)
    if not m or not m["on"]:
        label = m["label"] if m else "This part of the hub"
        return {"code": "off", "feature": feature, "label": label, "message": f"{label} is switched off right now."}
    if m.get("grants_only"):
        return {"code": "off", "feature": feature, "label": m["label"], "message": f"{m['label']} isn't open on your account."}
    minimum, label = m["tier"], m["label"]
    tier = plans.PLANS[minimum]
    return {"code": "locked", "feature": feature, "label": label, "what": m["what"], "tier": minimum, "tier_name": tier["name"],
            "price_minor": tier["price_minor"], "period": tier["period"],
            "message": f"{label} is part of {tier['name']}."}


async def get_ctx(user: dict = Depends(current_user)) -> Ctx:
    if user.get("team_of"):
        owner = await db().users.find_one({"_id": user["team_of"]})
        if not owner or owner.get("disabled") or not plans.has(owner, "team_logins"):
            raise HTTPException(403, "Your team access is paused. Please ask the business owner.")
        return Ctx(user, owner, user.get("team_role", "staff"))
    return Ctx(user, user, "owner")


async def owner_ctx(ctx: Ctx = Depends(get_ctx)) -> Ctx:
    if ctx.role != "owner":
        raise HTTPException(403, "Only the business owner can do this.")
    return ctx


async def manager_ctx(ctx: Ctx = Depends(get_ctx)) -> Ctx:
    if not ctx.at_least("manager"):
        raise HTTPException(403, "Ask your manager or the business owner to do this.")
    return ctx


def feature(key: str, role: str = "staff"):
    """Dependency: the plan must include `key` and the person must have at least `role`."""
    async def dep(ctx: Ctx = Depends(get_ctx)) -> Ctx:
        ctx.require(key)
        if not ctx.at_least(role):
            raise HTTPException(403, "Only the business owner can do this." if role == "owner"
                                else "Ask your manager or the business owner to do this.")
        return ctx
    return dep
