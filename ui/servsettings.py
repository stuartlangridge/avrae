import abc
import asyncio
from contextlib import suppress
from typing import List, Optional, TYPE_CHECKING, TypeVar

import d20
import disnake

from utils.aldclient import discord_user_to_dict
from utils.constants import STAT_ABBREVIATIONS
from utils.functions import natural_join
from utils.settings.guild import InlineRollingType, ServerSettings, RandcharRule
from .menu import MenuBase

_AvraeT = TypeVar("_AvraeT", bound=disnake.Client)
if TYPE_CHECKING:
    from dbot import Avrae

    _AvraeT = Avrae

TOO_MANY_ROLES_SENTINEL = "__special:too_many_roles"


def get_over_under_desc(rules) -> str:
    if not rules:
        return "None"
    out = []
    for rule in rules:
        out.append(f"{rule.amount} {'over' if rule.type == 'gt' else 'under'} {rule.value}")
    return f"At least {', '.join(out)}"


def stat_names_desc(stat_names: list) -> str:
    return ", ".join(stat_names or [stat.upper() for stat in STAT_ABBREVIATIONS])


class ServerSettingsMenuBase(MenuBase, abc.ABC):
    __menu_copy_attrs__ = ("bot", "settings", "guild")
    bot: _AvraeT
    settings: ServerSettings
    guild: disnake.Guild

    async def commit_settings(self):
        """Commits any changed guild settings to the db."""
        await self.settings.commit(self.bot.mdb)

    async def get_inline_rolling_desc(self) -> str:
        flag_enabled = await self.bot.ldclient.variation(
            "cog.dice.inline_rolling.enabled", user=discord_user_to_dict(self.owner), default=False
        )
        if not flag_enabled:
            return "Inline rolling is currently **globally disabled** for all users. Check back soon!"

        if self.settings.inline_enabled == InlineRollingType.DISABLED:
            return "Inline rolling is currently **disabled**."
        elif self.settings.inline_enabled == InlineRollingType.REACTION:
            return (
                "Inline rolling is currently set to **react**. I'll look for messages containing `[[dice]]` "
                "and react with :game_die: - click the reaction to roll!"
            )
        return "Inline rolling is currently **enabled**. I'll roll any `[[dice]]` I find in messages!"


class ServerSettingsUI(ServerSettingsMenuBase):
    @classmethod
    def new(cls, bot: _AvraeT, owner: disnake.User, settings: ServerSettings, guild: disnake.Guild):
        inst = cls(owner=owner)
        inst.bot = bot
        inst.settings = settings
        inst.guild = guild
        return inst

    @disnake.ui.button(label="Lookup Settings", style=disnake.ButtonStyle.primary)
    async def lookup_settings(self, _: disnake.ui.Button, interaction: disnake.Interaction):
        await self.defer_to(_LookupSettingsUI, interaction)

    @disnake.ui.button(label="Inline Rolling Settings", style=disnake.ButtonStyle.primary)
    async def inline_rolling_settings(self, _: disnake.ui.Button, interaction: disnake.Interaction):
        await self.defer_to(_InlineRollingSettingsUI, interaction)

    @disnake.ui.button(label="Randchar Settings", style=disnake.ButtonStyle.primary)
    async def randchar_settings(self, _: disnake.ui.Button, interaction: disnake.Interaction):
        await self.defer_to(_RandcharSettingsUI, interaction)

    @disnake.ui.button(label="Miscellaneous Settings", style=disnake.ButtonStyle.primary)
    async def miscellaneous_settings(self, _: disnake.ui.Button, interaction: disnake.Interaction):
        await self.defer_to(_MiscellaneousSettingsUI, interaction)

    @disnake.ui.button(label="Exit", style=disnake.ButtonStyle.danger)
    async def exit(self, *_):
        await self.on_timeout()

    async def get_content(self):
        embed = disnake.Embed(title=f"Server Settings for {self.guild.name}", colour=disnake.Colour.blurple())
        if self.settings.dm_roles:
            dm_roles = natural_join([f"<@&{role_id}>" for role_id in self.settings.dm_roles], "or")
        else:
            dm_roles = "Dungeon Master, DM, Game Master, or GM"
        embed.add_field(
            name="Lookup Settings",
            value=f"**DM Roles**: {dm_roles}\n"
            f"**Monsters Require DM**: {self.settings.lookup_dm_required}\n"
            f"**Direct Message DM**: {self.settings.lookup_pm_dm}\n"
            f"**Direct Message Results**: {self.settings.lookup_pm_result}",
            inline=False,
        )
        embed.add_field(name="Inline Rolling Settings", value=await self.get_inline_rolling_desc(), inline=False)

        embed.add_field(
            name="Randchar Settings",
            value=f"**Dice**: {self.settings.randchar_dice}\n"
            f"**Number of Sets**: {self.settings.randchar_sets}\n"
            f"**Assign Stats**: {self.settings.randchar_straight}\n"
            f"**Stat Names:** {stat_names_desc(self.settings.randchar_stat_names)}\n"
            f"**Minimum Total**: {self.settings.randchar_min}\n"
            f"**Maximum Total**: {self.settings.randchar_max}\n"
            f"**Over/Under Rules**: {get_over_under_desc(self.settings.randchar_rules)}",
            inline=False,
        )

        nlp_enabled_description = ""
        nlp_feature_flag = await self.bot.ldclient.variation(
            "cog.initiative.upenn_nlp.enabled", user=discord_user_to_dict(self.owner), default=False
        )
        if nlp_feature_flag:
            nlp_enabled_description = f"\n**Contribute Message Data to NLP Training**: {self.settings.upenn_nlp_opt_in}"
        embed.add_field(
            name="Miscellaneous Settings",
            value=f"**Show DDB Campaign Message**: {self.settings.show_campaign_cta}" f"{nlp_enabled_description}",
            inline=False,
        )

        return {"embed": embed}


class _LookupSettingsUI(ServerSettingsMenuBase):
    select_dm_roles: disnake.ui.Select  # make the type checker happy

    # ==== ui ====
    @disnake.ui.select(placeholder="Select DM Roles", min_values=0)
    async def select_dm_roles(self, select: disnake.ui.Select, interaction: disnake.Interaction):
        if len(select.values) == 1 and select.values[0] == TOO_MANY_ROLES_SENTINEL:
            role_ids = await self._text_select_dm_roles(interaction)
        else:
            role_ids = list(map(int, select.values))
        self.settings.dm_roles = role_ids or None
        self._refresh_dm_role_select()
        await self.commit_settings()
        await self.refresh_content(interaction)

    @disnake.ui.button(label="Toggle Monsters Require DM", style=disnake.ButtonStyle.primary, row=1)
    async def toggle_dm_required(self, _: disnake.ui.Button, interaction: disnake.Interaction):
        self.settings.lookup_dm_required = not self.settings.lookup_dm_required
        await self.commit_settings()
        await self.refresh_content(interaction)

    @disnake.ui.button(label="Toggle Direct Message DMs", style=disnake.ButtonStyle.primary, row=1)
    async def toggle_pm_dm(self, _: disnake.ui.Button, interaction: disnake.Interaction):
        self.settings.lookup_pm_dm = not self.settings.lookup_pm_dm
        await self.commit_settings()
        await self.refresh_content(interaction)

    @disnake.ui.button(label="Toggle Direct Message Results", style=disnake.ButtonStyle.primary, row=1)
    async def toggle_pm_result(self, _: disnake.ui.Button, interaction: disnake.Interaction):
        self.settings.lookup_pm_result = not self.settings.lookup_pm_result
        await self.commit_settings()
        await self.refresh_content(interaction)

    @disnake.ui.button(label="Back", style=disnake.ButtonStyle.grey, row=4)
    async def back(self, _: disnake.ui.Button, interaction: disnake.Interaction):
        await self.defer_to(ServerSettingsUI, interaction)

    # ==== handlers ====
    async def _text_select_dm_roles(self, interaction: disnake.Interaction) -> Optional[List[int]]:
        self.select_dm_roles.disabled = True
        await self.refresh_content(interaction)
        await interaction.send(
            "Choose the DM roles by sending a message to this channel. You can mention the roles, or use a "
            "comma-separated list of role names or IDs. Type `reset` to reset the role list to the default.",
            ephemeral=True,
        )

        try:
            input_msg: disnake.Message = await self.bot.wait_for(
                "message",
                timeout=60,
                check=lambda msg: msg.author == interaction.author and msg.channel.id == interaction.channel_id,
            )
            with suppress(disnake.HTTPException):
                await input_msg.delete()

            if input_msg.content == "reset":
                await interaction.send("The DM roles have been updated.", ephemeral=True)
                return None

            role_ids = {r.id for r in input_msg.role_mentions}
            for stmt in input_msg.content.split(","):
                clean_stmt = stmt.strip()
                try:  # get role by id
                    role_id = int(clean_stmt)
                    maybe_role = self.guild.get_role(role_id)
                except ValueError:  # get role by name
                    maybe_role = next((r for r in self.guild.roles if r.name.lower() == clean_stmt.lower()), None)
                if maybe_role is not None:
                    role_ids.add(maybe_role.id)

            if role_ids:
                await interaction.send("The DM roles have been updated.", ephemeral=True)
                return list(role_ids)
            await interaction.send("No valid roles found. Use the select menu to try again.", ephemeral=True)
            return self.settings.dm_roles
        except asyncio.TimeoutError:
            await interaction.send("No valid roles found. Use the select menu to try again.", ephemeral=True)
            return self.settings.dm_roles
        finally:
            self.select_dm_roles.disabled = False

    # ==== content ====
    def _refresh_dm_role_select(self):
        """Update the options in the DM Role select to reflect the currently selected values."""
        self.select_dm_roles.options.clear()
        if len(self.guild.roles) > 25:
            self.select_dm_roles.add_option(
                label="Whoa, this server has a lot of roles! Click here to select them.", value=TOO_MANY_ROLES_SENTINEL
            )
            return

        for role in reversed(self.guild.roles):  # display highest-first
            selected = self.settings.dm_roles is not None and role.id in self.settings.dm_roles
            self.select_dm_roles.add_option(label=role.name, value=str(role.id), emoji=role.emoji, default=selected)
        self.select_dm_roles.max_values = len(self.select_dm_roles.options)

    async def _before_send(self):
        self._refresh_dm_role_select()

    async def get_content(self):
        embed = disnake.Embed(
            title=f"Server Settings ({self.guild.name}) / Lookup Settings",
            colour=disnake.Colour.blurple(),
            description="These settings affect how lookup results are displayed on this server.",
        )
        if not self.settings.dm_roles:
            embed.add_field(
                name="DM Roles",
                value=f"**Dungeon Master, DM, Game Master, or GM**\n"
                f"*Any user with a role named one of these will be considered a DM. This lets them look up a "
                f"monster's full stat block if `Monsters Require DM` is enabled, skip other players' turns in "
                f"initiative, and more.*",
                inline=False,
            )
        else:
            dm_roles = natural_join([f"<@&{role_id}>" for role_id in self.settings.dm_roles], "or")
            embed.add_field(
                name="DM Roles",
                value=f"**{dm_roles}**\n"
                f"*Any user with at least one of these roles will be considered a DM. This lets them look up a "
                f"monster's full stat block if `Monsters Require DM` is enabled, skip turns in initiative, and "
                f"more.*",
                inline=False,
            )
        embed.add_field(
            name="Monsters Require DM",
            value=f"**{self.settings.lookup_dm_required}**\n"
            f"*If this is enabled, monster lookups will display hidden stats for any user without "
            f"a role named DM, GM, Dungeon Master, Game Master, or the DM role configured above.*",
            inline=False,
        )
        embed.add_field(
            name="Direct Message DMs",
            value=f"**{self.settings.lookup_pm_dm}**\n"
            f"*If this is enabled, the result of monster lookups will be direct messaged to the user who looked "
            f"it up, rather than being printed to the channel, if the user is a DM.*",
            inline=False,
        )
        embed.add_field(
            name="Direct Message Results",
            value=f"**{self.settings.lookup_pm_result}**\n"
            f"*If this is enabled, the result of all lookups will be direct messaged to the user who looked "
            f"it up, rather than being printed to the channel.*",
            inline=False,
        )
        return {"embed": embed}


class _InlineRollingSettingsUI(ServerSettingsMenuBase):
    @disnake.ui.button(label="Disable", style=disnake.ButtonStyle.primary)
    async def disable(self, button: disnake.ui.Button, interaction: disnake.Interaction):
        self.settings.inline_enabled = InlineRollingType.DISABLED
        button.disabled = True
        self.react.disabled = False
        self.enable.disabled = False
        await self.commit_settings()
        await self.refresh_content(interaction)

    @disnake.ui.button(label="React", style=disnake.ButtonStyle.primary)
    async def react(self, button: disnake.ui.Button, interaction: disnake.Interaction):
        self.settings.inline_enabled = InlineRollingType.REACTION
        button.disabled = True
        self.disable.disabled = False
        self.enable.disabled = False
        await self.commit_settings()
        await self.refresh_content(interaction)

    @disnake.ui.button(label="Enable", style=disnake.ButtonStyle.primary)
    async def enable(self, button: disnake.ui.Button, interaction: disnake.Interaction):
        self.settings.inline_enabled = InlineRollingType.ENABLED
        button.disabled = True
        self.disable.disabled = False
        self.react.disabled = False
        await self.commit_settings()
        await self.refresh_content(interaction)

    @disnake.ui.button(label="Back", style=disnake.ButtonStyle.grey, row=1)
    async def back(self, _: disnake.ui.Button, interaction: disnake.Interaction):
        await self.defer_to(ServerSettingsUI, interaction)

    async def _before_send(self):
        if self.settings.inline_enabled is InlineRollingType.DISABLED:
            self.disable.disabled = True
        elif self.settings.inline_enabled is InlineRollingType.REACTION:
            self.react.disabled = True
        elif self.settings.inline_enabled is InlineRollingType.ENABLED:
            self.enable.disabled = True

    async def get_content(self):
        embed = disnake.Embed(
            title=f"Server Settings ({self.guild.name}) / Inline Rolling Settings",
            colour=disnake.Colour.blurple(),
            description=await self.get_inline_rolling_desc(),
        )
        return {"embed": embed}


class _MiscellaneousSettingsUI(ServerSettingsMenuBase):
    # ==== ui ====
    @disnake.ui.button(label="Toggle DDB Campaign Message", style=disnake.ButtonStyle.primary, row=1)
    async def toggle_campaign_cta(self, _: disnake.ui.Button, interaction: disnake.Interaction):
        self.settings.show_campaign_cta = not self.settings.show_campaign_cta
        await self.commit_settings()
        await self.refresh_content(interaction)

    @disnake.ui.button(label="Toggle NLP Opt In", style=disnake.ButtonStyle.primary, row=1)
    async def toggle_upenn_nlp_opt_in(self, _: disnake.ui.Button, interaction: disnake.Interaction):
        self.settings.upenn_nlp_opt_in = not self.settings.upenn_nlp_opt_in
        await self.commit_settings()
        await self.refresh_content(interaction)

    @disnake.ui.button(label="Back", style=disnake.ButtonStyle.grey, row=4)
    async def back(self, _: disnake.ui.Button, interaction: disnake.Interaction):
        await self.defer_to(ServerSettingsUI, interaction)

    # ==== content ====
    async def _before_send(self):
        if TYPE_CHECKING:
            self.toggle_upenn_nlp_opt_in: disnake.ui.Button

        # nlp feature flag
        flag_enabled = await self.bot.ldclient.variation(
            "cog.initiative.upenn_nlp.enabled", user=discord_user_to_dict(self.owner), default=False
        )
        if not flag_enabled:
            self.remove_item(self.toggle_upenn_nlp_opt_in)

    async def get_content(self):
        embed = disnake.Embed(
            title=f"Server Settings ({self.guild.name}) / Miscellaneous Settings",
            colour=disnake.Colour.blurple(),
        )
        embed.add_field(
            name="Show DDB Campaign Message",
            value=f"**{self.settings.show_campaign_cta}**\n"
            f"*If this is enabled, you will receive occasional reminders to link your D&D Beyond campaign when "
            f"you import a character in an unlinked campaign.*",
            inline=False,
        )

        nlp_feature_flag = await self.bot.ldclient.variation(
            "cog.initiative.upenn_nlp.enabled", user=discord_user_to_dict(self.owner), default=False
        )
        if nlp_feature_flag:
            embed.add_field(
                name="Contribute Message Data to Natural Language AI Training",
                value=f"**{self.settings.upenn_nlp_opt_in}**\n"
                f"*If this is enabled, the contents of messages, usernames, character names, and snapshots "
                f"of a character's resources will be recorded in channels **with an active combat.***\n"
                f"*This data will be used in a project to make advances in interactive fiction and text "
                f"generation using artificial intelligence at the University of Pennsylvania.*\n"
                f"*Read more about the project [here](https://www.cis.upenn.edu/~ccb/language-to-avrae.html), "
                f"and our data handling and Privacy Policy [here](https://www.fandom.com/privacy-policy).*",
                inline=False,
            )

        return {"embed": embed}


class _RandcharSettingsUI(ServerSettingsMenuBase):
    # ==== ui ====
    @disnake.ui.button(label="Set Dice", style=disnake.ButtonStyle.primary)
    async def select_dice(self, button: disnake.ui.Button, interaction: disnake.Interaction):
        button.disabled = True
        await self.refresh_content(interaction)
        await interaction.send(
            "Choose a new dice string to roll by sending a message in this channel.",
            ephemeral=True,
        )
        try:
            input_msg: disnake.Message = await self.bot.wait_for(
                "message",
                timeout=60,
                check=lambda msg: msg.author == interaction.author and msg.channel.id == interaction.channel_id,
            )
            self.settings.randchar_dice = str(d20.parse(input_msg.content))
            with suppress(disnake.HTTPException):
                await input_msg.delete()
        except (ValueError, asyncio.TimeoutError, d20.errors.RollSyntaxError):
            await interaction.send("No valid dice found. Press `Set Dice` to try again.", ephemeral=True)
        else:
            await self.commit_settings()
            await interaction.send("Your dice have been updated.", ephemeral=True)
        finally:
            button.disabled = False
            await self.refresh_content(interaction)

    @disnake.ui.button(label="Set Number of Sets", style=disnake.ButtonStyle.primary)
    async def select_sets(self, button: disnake.ui.Button, interaction: disnake.Interaction):
        button.disabled = True
        await self.refresh_content(interaction)
        await interaction.send(
            "Choose a new number of sets to roll by sending a message in this channel.",
            ephemeral=True,
        )
        try:
            input_msg: disnake.Message = await self.bot.wait_for(
                "message",
                timeout=60,
                check=lambda msg: msg.author == interaction.author and msg.channel.id == interaction.channel_id,
            )
            if not 1 <= int(input_msg.content) <= 25:
                raise ValueError
            self.settings.randchar_sets = int(input_msg.content)
            with suppress(disnake.HTTPException):
                await input_msg.delete()
        except (ValueError, asyncio.TimeoutError):
            await interaction.send(
                "No valid number of sets found. Press `Set Number of Sets` to try again.", ephemeral=True
            )
        else:
            await self.commit_settings()
            await interaction.send("Your number of sets have been updated.", ephemeral=True)
        finally:
            button.disabled = False
            await self.refresh_content(interaction)

    @disnake.ui.button(label="Set Number of Stats", style=disnake.ButtonStyle.primary)
    async def select_stats(self, button: disnake.ui.Button, interaction: disnake.Interaction):
        button.disabled = True
        await self.refresh_content(interaction)
        await interaction.send(
            "Choose a new number of stats to roll by sending a message in this channel.",
            ephemeral=True,
        )
        try:
            input_msg: disnake.Message = await self.bot.wait_for(
                "message",
                timeout=60,
                check=lambda msg: msg.author == interaction.author and msg.channel.id == interaction.channel_id,
            )
            if not 1 <= int(input_msg.content) <= 10:
                raise ValueError
            self.settings.randchar_num = int(input_msg.content)
            if self.settings.randchar_num != len(self.settings.randchar_stat_names):
                self.settings.randchar_straight = False
            with suppress(disnake.HTTPException):
                await input_msg.delete()
        except (ValueError, asyncio.TimeoutError):
            await interaction.send(
                "No valid number of stats found. Press `Set Number of Stats` to try again.", ephemeral=True
            )
        else:
            await self.commit_settings()
            await interaction.send("Your number of stats have been updated.", ephemeral=True)
        finally:
            button.disabled = False
            await self.refresh_content(interaction)

    @disnake.ui.button(label="Toggle Assign Stats", style=disnake.ButtonStyle.primary)
    async def toggle_straight(self, button: disnake.ui.Button, interaction: disnake.Interaction):
        self.settings.randchar_straight = not self.settings.randchar_straight
        if self.settings.randchar_straight:
            button.disabled = True
            await self.refresh_content(interaction)
            await interaction.send(
                "Choose the stat names to automatically assign the rolled stats to, separated by commas.\n"
                "If you wish to use the default stats, respond with 'default'. This will only work if your number "
                "of stats is 6.",
                ephemeral=True,
            )
            try:
                input_msg: disnake.Message = await self.bot.wait_for(
                    "message",
                    timeout=60,
                    check=lambda msg: msg.author == interaction.author and msg.channel.id == interaction.channel_id,
                )
                message = input_msg.content
                if message.lower() == "default":
                    stats = [stat.upper() for stat in STAT_ABBREVIATIONS]
                else:
                    stats = message.replace(", ", ",").split(",")
                if len(stats) != self.settings.randchar_num:
                    raise ValueError
                self.settings.randchar_stat_names = stats
                with suppress(disnake.HTTPException):
                    await input_msg.delete()
            except (ValueError, asyncio.TimeoutError):
                self.settings.randchar_straight = False
                await interaction.send(
                    "Invalid stat names over found. Press `Toggle Assign Stats` to try again.", ephemeral=True
                )
            else:
                await self.commit_settings()
                await interaction.send("Your stat names have been updated.", ephemeral=True)
            finally:
                button.disabled = False
        await self.commit_settings()
        await self.refresh_content(interaction)

    @disnake.ui.button(label="Set Minimum", style=disnake.ButtonStyle.primary, row=1)
    async def select_minimum(self, button: disnake.ui.Button, interaction: disnake.Interaction):
        button.disabled = True
        await self.refresh_content(interaction)
        await interaction.send(
            "Choose a new minimum roll total by sending a message in this channel. To reset it, respond with 'default'.",
            ephemeral=True,
        )
        try:
            input_msg: disnake.Message = await self.bot.wait_for(
                "message",
                timeout=60,
                check=lambda msg: msg.author == interaction.author and msg.channel.id == interaction.channel_id,
            )
            if input_msg.content.lower() == "default":
                self.settings.randchar_min = None
            else:
                self.settings.randchar_min = int(input_msg.content)
            with suppress(disnake.HTTPException):
                await input_msg.delete()
        except (ValueError, asyncio.TimeoutError):
            await interaction.send("No valid minimum found. Press `Set Minimum` to try again.", ephemeral=True)
        else:
            await self.commit_settings()
            await interaction.send("Your minimum score has been updated.", ephemeral=True)
        finally:
            button.disabled = False
            await self.refresh_content(interaction)

    @disnake.ui.button(label="Set Maximum", style=disnake.ButtonStyle.primary, row=1)
    async def select_maximum(self, button: disnake.ui.Button, interaction: disnake.Interaction):
        button.disabled = True
        await self.refresh_content(interaction)
        await interaction.send(
            "Choose a new maximum roll total by sending a message in this channel. To reset it, respond with 'default'.",
            ephemeral=True,
        )
        try:
            input_msg: disnake.Message = await self.bot.wait_for(
                "message",
                timeout=60,
                check=lambda msg: msg.author == interaction.author and msg.channel.id == interaction.channel_id,
            )
            if input_msg.content.lower() == "default":
                self.settings.randchar_max = None
            else:
                self.settings.randchar_max = int(input_msg.content)
            with suppress(disnake.HTTPException):
                await input_msg.delete()
        except (ValueError, asyncio.TimeoutError):
            await interaction.send("No valid maximum found. Press `Set Maximum` to try again.", ephemeral=True)
        else:
            await self.commit_settings()
            await interaction.send("Your maximum score has been updated.", ephemeral=True)
        finally:
            button.disabled = False
            await self.refresh_content(interaction)

    @disnake.ui.button(label="Add Over/Under Rule", style=disnake.ButtonStyle.primary, row=1)
    async def add_rule(self, button: disnake.ui.Button, interaction: disnake.Interaction):
        button.disabled = True
        await self.refresh_content(interaction)
        await interaction.send(
            "Choose a new required over by sending a message in this channel.\n"
            "Please use the format 'number>score' or 'number<score', for example '1>15' for at least one over 15, or "
            "'2<10' for at least two under 10.",
            ephemeral=True,
        )
        try:
            input_msg: disnake.Message = await self.bot.wait_for(
                "message",
                timeout=60,
                check=lambda msg: msg.author == interaction.author and msg.channel.id == interaction.channel_id,
            )
            if ">" in input_msg.content:
                rule_type = "gt"
                amount, value = input_msg.content.split(">")
                new_rule = RandcharRule(type=rule_type, amount=amount, value=value)
            elif "<" in input_msg.content:
                rule_type = "lt"
                amount, value = input_msg.content.split("<")
                new_rule = RandcharRule(type=rule_type, amount=amount, value=value)
            else:
                raise ValueError
            self.settings.randchar_rules.append(new_rule)
            self._refresh_remove_rule_select()
            with suppress(disnake.HTTPException):
                await input_msg.delete()
        except (ValueError, asyncio.TimeoutError):
            await interaction.send(
                "No valid over/under rule found. Press `Add Over/Under Rule` to try again.", ephemeral=True
            )
        else:
            await self.commit_settings()
            await interaction.send("Your required over/under rules has been updated.", ephemeral=True)
        finally:
            if len(self.settings.randchar_rules) < 25:
                button.disabled = False
            await self.refresh_content(interaction)

    @disnake.ui.select(placeholder="Remove Rule", min_values=0, max_values=1, row=3)
    async def remove_rule(self, select: disnake.ui.Select, interaction: disnake.Interaction):
        removed_rule = int(select.values[0])
        self.settings.randchar_rules.pop(removed_rule)
        self._refresh_remove_rule_select()
        await self.commit_settings()
        await self.refresh_content(interaction)

    @disnake.ui.button(label="Back", style=disnake.ButtonStyle.grey, row=4)
    async def back(self, _: disnake.ui.Button, interaction: disnake.Interaction):
        await self.defer_to(ServerSettingsUI, interaction)

    # ==== content ====
    def _refresh_remove_rule_select(self):
        """Update the options in the Remove Rule select to reflect the currently available values."""
        self.remove_rule.options.clear()
        if not self.settings.randchar_rules:
            self.remove_rule.add_option(label="Empty")
            self.remove_rule.disabled = True
            return
        self.remove_rule.disabled = False
        if len(self.settings.randchar_rules) < 25:
            self.add_rule.disabled = False
        else:
            self.add_rule.disabled = True
        for i, rule in enumerate(self.settings.randchar_rules):
            self.remove_rule.add_option(
                label=f"{rule.amount} {'over' if rule.type == 'gt' else 'under'} {rule.value}", value=str(i)
            )

    async def _before_send(self):
        self._refresh_remove_rule_select()

    async def get_content(self):
        embed = disnake.Embed(
            title=f"Server Settings ({self.guild.name}) / Randchar Settings",
            colour=disnake.Colour.blurple(),
        )
        embed.add_field(
            name="Dice Rolled",
            value=f"**{self.settings.randchar_dice}**\n"
            f"*This is the dice string that will be rolled six times for "
            f"each stat set.*",
            inline=False,
        )
        embed.add_field(
            name="Number of Sets",
            value=f"**{self.settings.randchar_sets}**\n"
            f"*This is how many sets of stat rolls it will return, "
            f"allowing your players to choose between them.*",
            inline=False,
        )
        embed.add_field(
            name="Number of Stats",
            value=f"**{self.settings.randchar_num}**\n"
            f"*This is how many stat rolls it will return per set, "
            f"allowing your players to choose between them.*",
            inline=False,
        )
        embed.add_field(
            name="Assign Stats Directly",
            value=f"**{self.settings.randchar_straight}**\n"
            f"**Stat Names:** {stat_names_desc(self.settings.randchar_stat_names)}\n"
            f"*If this is enabled, stats will automatically be assigned to stats in the order "
            f"they are rolled.*",
            inline=False,
        )
        embed.add_field(
            name="Minimum Total Score Required",
            value=f"**{self.settings.randchar_min}**\n"
            f"*This is the minimum combined score required. Standard array is 72 total.*",
            inline=False,
        )
        embed.add_field(
            name="Maximum Total Score Required",
            value=f"**{self.settings.randchar_max}**\n"
            f"*This is the maximum combined score required. Standard array is 72 total.*",
            inline=False,
        )
        embed.add_field(
            name="Over/Under Rules",
            value=f"**{get_over_under_desc(self.settings.randchar_rules)}**\n"
            f"*This is a list of how many of the stats you require to be over/under a certain value, "
            f"such as having at least one stat over 17, or two stats under 10.*",
            inline=False,
        )

        return {"embed": embed}
