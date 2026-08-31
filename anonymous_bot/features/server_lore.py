"""Unlimited Discord server lore collector."""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
import discord
from ..config import GM_USER_IDS, SERVER_LORE_CHANNELS, SERVER_LORE_COLLECTION, GAME_GUILD_ID
from ..core import campaign_store, lore_index

def _is_gm(user):
    return str(getattr(user, "id", "")) in {str(x) for x in GM_USER_IDS}

def _allowed_channel(channel):
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        return False
    if SERVER_LORE_CHANNELS:
        allowed={x.strip() for x in SERVER_LORE_CHANNELS.split(",") if x.strip()}
        return str(channel.id) in allowed or getattr(channel,"name","") in allowed
    return True

def archive(message):
    if not SERVER_LORE_COLLECTION or not getattr(message,"guild",None) or getattr(message.author,"bot",False): return
    # Lore collection is intentionally restricted to the main campaign server.
    if int(message.guild.id) != int(GAME_GUILD_ID): return
    if not _allowed_channel(message.channel): return
    content=(message.content or "").strip()
    if not content and not getattr(message,"attachments",None): return
    # Everyone's messages are evidence; GM/writer messages get a priority flag for
    # retrieval, but even GM messages are not automatically canon because they may
    # be jokes, roleplay, hypotheticals, or speculation. The AI performs the final
    # authority/context check.
    campaign_store.archive_message(message, priority=_is_gm(message.author))

async def _sync_channel(channel):
    if not _allowed_channel(channel): return 0
    # A channel is backfilled only once. The sync marker is written only after
    # the complete Discord history walk finishes successfully.
    existing_sync = campaign_store.get_server_sync(channel.guild.id, channel.id)
    if existing_sync and existing_sync.get("last_sync_at"):
        print(f"[server-lore] SKIP #{getattr(channel,'name','?')}: already backfilled ({existing_sync.get('messages_synced', 0):,} messages)")
        return 0
    me=getattr(channel.guild,"me",None)
    if me is not None:
        perms=channel.permissions_for(me)
        if not (perms.view_channel and perms.read_message_history):
            print(f"[server-lore] SKIP #{getattr(channel,'name','?')}: missing View Channel or Read Message History")
            return 0
    count=0; oldest=None
    try:
        # limit=None means Discord history is walked all the way to the oldest message.
        async for message in channel.history(limit=None, oldest_first=True):
            if message.author.bot: continue
            content=(message.content or "").strip()
            if not content and not getattr(message,"attachments",None): continue
            # Everyone's messages are evidence; GM/writer messages get a priority flag for
            # retrieval, but even GM messages are not automatically canon because they may
            # be jokes, roleplay, hypotheticals, or speculation. The AI performs the final
            # authority/context check.
            await asyncio.to_thread(campaign_store.archive_message, message, priority=_is_gm(message.author))
            count += 1; oldest=message.created_at.isoformat()
            if count % 500 == 0: print(f"[server-lore] #{getattr(channel,'name','?')}: archived {count:,}...")
        campaign_store.upsert_server_sync(channel.guild.id,channel.id,getattr(channel,"name",""),datetime.now(timezone.utc).isoformat(),oldest,count)
        print(f"[server-lore] DONE #{getattr(channel,'name','?')}: {count:,} messages")
        return count
    except discord.Forbidden as exc:
        print(f"[server-lore] FORBIDDEN #{getattr(channel,'name','?')}: {exc}")
    except discord.HTTPException as exc:
        print(f"[server-lore] HTTP ERROR #{getattr(channel,'name','?')}: {exc}")
    except Exception as exc:
        print(f"[server-lore] ERROR #{getattr(channel,'name','?')}: {type(exc).__name__}: {exc}")
    return 0

async def _forum_threads(channel):
    threads=[]
    try:
        for thread in await channel.threads(): threads.append(thread)
    except Exception: pass
    try:
        async for thread in channel.archived_threads(limit=None): threads.append(thread)
    except Exception as exc:
        print(f"[server-lore] Forum archive warning #{getattr(channel,'name','?')}: {type(exc).__name__}: {exc}")
    seen=set()
    return [t for t in threads if not (t.id in seen or seen.add(t.id))]

async def sync_guild(guild):
    if not SERVER_LORE_COLLECTION: return
    # Never backfill testing/dev servers. Only the configured main campaign guild.
    if int(guild.id) != int(GAME_GUILD_ID):
        print(f"[server-lore] SKIP SERVER: {guild.name} ({guild.id}) is not the main campaign server ({GAME_GUILD_ID})")
        return
    print(f"[server-lore] ===== FULL SERVER BACKFILL: {guild.name} ({guild.id}) =====")
    total=0; channels=[]
    for ch in getattr(guild,"text_channels",[]): channels.append(ch)
    # Forum channels are not TextChannel objects, but their posts are Threads.
    for forum in getattr(guild,"forums",[]) or []:
        channels.extend(await _forum_threads(forum))
    # Active threads, including threads Discord exposes globally.
    for thread in list(getattr(guild,"threads",[]) or []): channels.append(thread)
    seen=set()
    for channel in channels:
        if channel.id in seen: continue
        seen.add(channel.id)
        total += await _sync_channel(channel)
        await asyncio.sleep(0)
    print(f"[server-lore] ===== FULL BACKFILL COMPLETE: {total:,} messages =====")

async def startup(bot):
    if not SERVER_LORE_COLLECTION:
        print("[server-lore] Collection disabled by SERVER_LORE_COLLECTION.")
        return
    for guild in bot.guilds:
        await sync_guild(guild)
        if int(guild.id) == int(GAME_GUILD_ID):
            try:
                result = lore_index.rebuild_from_database(guild.id, GM_USER_IDS)
                campaign_store.audit_event(guild.id, "lore_index_rebuilt", "system",
                    details=result)
                print(f"[server-lore] Structured lore index rebuilt: {result}")
            except Exception as exc:
                print(f"[server-lore] Structured lore rebuild warning: {type(exc).__name__}: {exc}")
