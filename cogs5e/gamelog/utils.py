import functools
import logging

from ddb.gamelog import GameLogEventContext
from ddb.gamelog.errors import IgnoreEvent


def feature_flag(flag_name, default=False):
    """
    Returns a decorator that checks the state of the given feature flag before calling the wrapped function.

    The inner function must be an async method that takes a parameter of type :class:`GameLogEventContext`
    as its first argument.

    .. note::
        Feature flag targeting can only be controlled by the global setting or individual user id - rules such as
        ``Admin in user.roles`` will always return the global setting as we do not have the full user loaded yet
    """

    def decorator(inner):
        @functools.wraps(inner)
        async def wrapped(self, gctx: GameLogEventContext, *args, **kwargs):
            # to avoid having to load the entire ddb user (slow!) we just check the user id in launchdarkly
            # the user must have connected their account to get here, so we don't have to worry about populating
            # custom attributes/username/etc
            # note: this means that feature flag targeting can only be controlled by global or individual user id
            # but still, better than nothing
            user = await gctx.bot.ddb.get_ddb_user(gctx, gctx.event.user_id)

            if not user:
                raise IgnoreEvent(f"User {gctx.event.user_id} has not connected their account")
            flag_on = await gctx.bot.ldclient.variation_for_ddb_user(flag_name, user, False, discord_id=gctx.get_discord_user())
            if not flag_on:
                raise IgnoreEvent(f"Feature flag {flag_name!r} is disabled for user {user.username}")
            return await inner(self, gctx, *args, **kwargs)

        return wrapped

    return decorator
