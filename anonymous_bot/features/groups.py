from discord import app_commands

# Shared top-level groups used by multiple feature modules.
# GM/admin-only commands live under /admin so permissions can be managed
# centrally without mixing them into player-facing command groups.
ADMIN_GROUP = app_commands.Group(name="admin", description="GM/admin campaign management commands.")
SESSION_GROUP = app_commands.Group(name="session", description="Player session records and status.")

# The visible session command is intentionally /gm game start (rather than a
# detached /gm-game command) so it matches the website's GM Start Session flow.
GM_GROUP = app_commands.Group(name="gm", description="GM campaign controls.")
ADMIN_GAME_GROUP = app_commands.Group(name="game", description="GM game session controls.", parent=GM_GROUP)
ADMIN_ATTENDANCE_GROUP = app_commands.Group(name="gm-attendance", description="GM attendance controls.")
ADMIN_SESSION_GROUP = app_commands.Group(name="gm-session", description="GM session logging and history.")
ADMIN_BOUNTY_GROUP = app_commands.Group(name="gm-bounty", description="GM bounty management.")
ADMIN_REPUTATION_GROUP = app_commands.Group(name="gm-reputation", description="GM reputation management.")
ADMIN_ITEM_GROUP = app_commands.Group(name="gm-item", description="GM item creation and drop controls.")
ADMIN_INVENTORY_GROUP = app_commands.Group(name="gm-inventory", description="GM inventory management.")
ADMIN_ECONOMY_GROUP = app_commands.Group(name="gm-economy", description="GM economy and shop controls.")
ADMIN_FACTION_GROUP = app_commands.Group(name="gm-faction", description="GM faction management.")
ADMIN_DUNGEON_GROUP = app_commands.Group(name="gm-dungeon", description="GM dungeon controls.")
ADMIN_MEMORY_GROUP = app_commands.Group(name="gm-memory", description="GM campaign memory controls.")
ADMIN_STORY_GROUP = app_commands.Group(name="gm-story", description="GM story and objective controls.")
ADMIN_HELL_GROUP = app_commands.Group(name="gm-hell", description="GM controls for Hell and its events.")
