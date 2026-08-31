import random, math
import discord
from discord import app_commands
from .items import item_state, save_item_data, ITEM_CATALOG, decorate_item, add_item, RARITIES, build_stats, qualitative_stats
from .equipment import combat_stats
from .groups import ADMIN_DUNGEON_GROUP

# Slash-command groups keep the command tree organized and below Discord's 100 global command limit.
DUNGEON_GROUP = app_commands.Group(name="dungeon", description="Dungeon commands.")


MAX_FLOOR = 1000

BOSSES = [
  ("The Warden", "warden", "A defensive knight who punishes reckless attacks."),
  ("Grave Knight", "grave", "Raises spectral blades and drains your speed."),
  ("Abyssal Beast", "beast", "A brutal monster with huge attacks and low accuracy."),
  ("Cursed Alchemist", "alchemist", "Uses poison and unstable potions."),
  ("Iron Colossus", "colossus", "Slow, armored, and extremely difficult to penetrate."),
  ("The Devourer", "devourer", "Gets stronger when the fight drags on."),
  ("Fallen Saint", "saint", "Uses radiant attacks and defensive blessings."),
  ("Void Hunter", "void", "A fast boss that attacks weak defenses."),
  ("Dungeon Tyrant", "tyrant", "A balanced endgame-style boss."),
  ("The Watcher", "watcher", "Predicts repeated attacks and punishes patterns."),
]
BOSS_LOOT = {
  "warden": ["Warden's Mantle", "Iron Warhammer"],
  "grave": ["Grave Knight's Blade", "Shadow Hood"],
  "beast": ["Beast Fang", "Dragonhide Vest"],
  "alchemist": ["Alchemist's Vial", "Cursed Dice"],
  "colossus": ["Tower Shield", "Iron Breastplate"],
  "devourer": ["Devourer's Greatsword", "Blood Crystal"],
  "saint": ["Fallen Saint's Mace", "Pilgrim's Robe"],
  "void": ["Void Hunter Bow", "Shadow Hood"],
  "tyrant": ["Tyrant's Blade", "Royal Guard Plate"],
  "watcher": ["Watcher Eye", "Runic Harness"],
}


def dstate(guild_id, user_id):
  st=item_state(guild_id); d=st.setdefault("dungeon",{"players":{},"locked":[]})
  d.setdefault("players",{}); d.setdefault("locked",[]); d.setdefault("leaderboard",{}); d.setdefault("prestige",{})
  p=d["players"].setdefault(str(user_id),{"floor":1,"position":0,"explored":False,"checkpoint":1,"death_protection":0,"event_done":False})
  for k,v in {"position":0,"checkpoint":1,"death_protection":0,"event_done":False}.items(): p.setdefault(k,v)
  # Remove legacy stamina fields from old saves.
  p.pop("stamina", None); p.pop("stamina_at", None)
  return d,p


def floor_info(floor):
  rng=random.Random(f"dungeon-floor-{floor}")
  terrain=rng.choice(["Ruined Hall","Flooded Cavern","Ashen Gallery","Forgotten Library","Crystal Vault","Rotting Keep","Sunken Shrine","Broken Foundry","Grave Passage","Storm Chamber","Mirror Maze","Fungal Grotto","Clockwork Ruins","Starfall Chapel"])
  boss=rng.choice(BOSSES)
  chest=(rng.random()<.55 if floor<=10 else rng.random()<min(.75,.20+floor/2200))
  event=rng.choice(["merchant","shrine","trap","npc","puzzle","secret_room","ambush","healing_pool"])
  return terrain,boss,chest,event


def rarity_for_floor(floor,bonus=False):
  # Depth guarantees access to better tiers; RNG only decides within the depth band.
  max_i=min(len(RARITIES)-1, 2+floor//10+(5 if bonus else 0))
  min_i=min(max_i,max(0,floor//25))
  return random.choice(RARITIES[min_i:max_i+1])


def make_loot(floor, bonus=False, boss_key=None):
  name=None
  if boss_key:
    candidates=BOSS_LOOT.get(boss_key,[])
    name=random.choice(candidates) if candidates else None
  base=next((x for x in ITEM_CATALOG if x.get("base_name")==name),None) if name else None
  if base is None:
    base=random.choice([x for x in ITEM_CATALOG if not x.get("custom_template")])
  item=decorate_item(base); rarity=rarity_for_floor(floor,bonus)
  item["rarity"]=rarity; item.pop("item_level", None); item["name"]=name or base["base_name"]
  item["stats"]=build_stats(rarity,base.get("category","item"),item.get("effect",""))
  item["description"]=(f"Recovered from dungeon floor {floor}." + (" Boss-specific loot." if boss_key else ""))
  return item


def boss_stats(floor,boss_key):
  base=(12+floor*1.15 if floor<=10 else 22+floor*1.8)
  mods={"warden":(1.0,1.35),"grave":(1.05,1.0),"beast":(1.35,.75),"alchemist":(.85,.9),"colossus":(.75,1.65),"devourer":(1.2,1.1),"saint":(1.0,1.25),"void":(1.3,.95),"tyrant":(1.2,1.2),"watcher":(1.1,1.1)}
  a,d=mods.get(boss_key,(1,1)); return {"attack":round(base*a),"defense":round(base*d),"hp":round(base*2.8)}


def combat_result(floor,boss_key,stats):
  b=boss_stats(floor,boss_key); attack=stats.get("attack",0); defense=stats.get("defense",0); accuracy=stats.get("accuracy",0); speed=stats.get("speed",0)
  early=floor<=10
  # Equipment creates the baseline; randomness is a modifier, not the deciding system.
  rounds=0; hp=b["hp"]
  while rounds<8 and hp>0:
    rounds+=1
    hit=max(.55,min(.97,.65+accuracy/300+speed/500))
    if random.random()<hit:
      damage=max(2,attack-b["defense"]*.25)
      hp-=damage
    incoming=b["attack"]*((.48+random.random()*.20) if early else (.72+random.random()*.35))
    incoming*=max(.35,1-defense/350)
    if boss_key=="grave" and rounds==3: incoming*=1.35
    if boss_key=="beast" and rounds>=4: incoming*=1.25
    if boss_key=="colossus": incoming*=.85
    if boss_key=="void": incoming*=1.1 if defense<floor else .8
    hp=max(0,hp)
    if hp<=0: return True,rounds
    # Effective player survivability score.
    if not early and incoming>attack*.95 and random.random()>.45: return False,rounds
  chance=max(.15,min(.95,(attack+defense*.45+accuracy*.25+speed*.15)/(b["attack"]+b["defense"]+b["hp"]*.12)))
  if early: chance=max(chance,.90-.02*(floor-1))
  return random.random()<chance,rounds


def leaderboard(d,guild_id):
  return sorted(d.get("leaderboard",{}).items(), key=lambda x:(x[1].get("floor",0),x[1].get("kills",0)), reverse=True)[:10]

def render_dungeon_text(guild_id, user_id):
  d,p=dstate(guild_id,user_id); floor=p["floor"]
  terrain,boss,chest,event=floor_info(floor); stats=combat_stats(guild_id,user_id)
  return (f" **DUNGEON — FLOOR {floor}/{MAX_FLOOR}**\n\n**Area:** {terrain} • **Boss:** {boss[0]}\n"
      f"**Chest:** {' Present' if chest else 'None'} • **Event:** {event.replace('_',' ').title()}\n"
      f"**Checkpoint:** {p.get('checkpoint',1)} • **Progress:** {p.get('position',0)}/3\n"
      + "\n".join(f"{label}: **{value}**" for label,value in qualitative_stats(stats)))

class DungeonView(discord.ui.View):
  def __init__(self,guild_id,user_id):
    super().__init__(timeout=900); self.guild_id=guild_id; self.user_id=user_id
  async def check(self,interaction):
    if interaction.user.id!=self.user_id:
      await interaction.response.send_message(" This dungeon screen belongs to another player.",ephemeral=True); return False
    return True
  async def refresh(self,interaction,note=""):
    d,p=dstate(self.guild_id,self.user_id); floor=p["floor"]; terrain,boss,chest,event=floor_info(floor); stats=combat_stats(self.guild_id,self.user_id)
    stat_text=" • ".join(f"{label}: **{value}**" for label,value in qualitative_stats(stats))
    text=(f" **DUNGEON — FLOOR {floor}/{MAX_FLOOR}**\n\n**Area:** {terrain}\n**Boss:** {boss[0]}\n**Chest:** {' Present' if chest else 'None detected'}\n**Event:** {event.replace('_',' ').title()}\n**Checkpoint:** {p.get('checkpoint',1)}\n**Progress:** {p.get('position',0)}/3\n{stat_text}")
    if note: text=f"{note}\n\n"+text
    await interaction.response.edit_message(content=text,view=self)
  @discord.ui.button(label="Move",style=discord.ButtonStyle.primary)
  async def move(self,interaction,button):
    if not await self.check(interaction): return
    d,p=dstate(self.guild_id,self.user_id); floor=p["floor"]
    if floor in d["locked"]: return await self.refresh(interaction," This floor is locked.")
    p["position"]+=1; p["explored"]=p["position"]>=3; save_item_data(); await self.refresh(interaction," You moved deeper." if p["position"]<3 else " You reached the boss chamber.")
  @discord.ui.button(label="Event",style=discord.ButtonStyle.secondary)
  async def event(self,interaction,button):
    if not await self.check(interaction): return
    d,p=dstate(self.guild_id,self.user_id); floor=p["floor"]
    if p.get("event_done"): return await self.refresh(interaction,"The floor event has already been resolved.")
    _,_,_,event=floor_info(floor); msg=""
    if event=="shrine": msg=" A shrine reveals a safe route through the floor."
    elif event=="healing_pool": msg=" A hidden pool restores your vitality for the upcoming battle."
    elif event=="trap": msg=" A trap slows your progress, but you push onward."
    elif event=="secret_room": loot=make_loot(floor,bonus=True); add_item(self.guild_id,self.user_id,loot,held=True); msg=f"Secret room! Found **{loot['name']}** — {loot['rarity']}."
    elif event=="merchant": msg="merchant A wandering merchant offers a temporary discount."
    elif event=="puzzle": msg=" You solved the puzzle and gained a hidden chest bonus."
    elif event=="npc": msg=" An explorer gives you a useful clue about the boss."
    else: msg=" An ambush! You survived and learned the boss is aggressive."
    p["event_done"]=True; save_item_data(); await self.refresh(interaction,msg)
  @discord.ui.button(label="Chest",style=discord.ButtonStyle.secondary)
  async def chest(self,interaction,button):
    if not await self.check(interaction): return
    d,p=dstate(self.guild_id,self.user_id); floor=p["floor"]
    _,_,chest,_=floor_info(floor)
    if not chest or p.get("chest_found_floor")==floor: return await self.refresh(interaction," No chest was found here.")
    p["chest_found_floor"]=floor; loot=make_loot(floor,bonus=True); add_item(self.guild_id,self.user_id,loot,held=True); save_item_data(); await self.refresh(interaction,f"**CHEST FOUND!** {loot['name']} — {loot['rarity']}")
  @discord.ui.button(label="Fight Boss",style=discord.ButtonStyle.danger)
  async def fight(self,interaction,button):
    if not await self.check(interaction): return
    d,p=dstate(self.guild_id,self.user_id); floor=p["floor"]
    if floor in d["locked"]: return await self.refresh(interaction," This floor is locked.")
    if p.get("position",0)<3: return await self.refresh(interaction," Reach the boss chamber first.")
    _,boss,_,_=floor_info(floor); stats=combat_stats(self.guild_id,self.user_id); won,rounds=combat_result(floor,boss[1],stats)
    if won:
      loot=make_loot(floor,bonus=True,boss_key=boss[1]); add_item(self.guild_id,self.user_id,loot,held=True); p["floor"]=min(MAX_FLOOR,floor+1); p["position"]=0; p["explored"]=False; p["event_done"]=False;
      if p["floor"]%10==1:p["checkpoint"]=p["floor"]
      lb=d.setdefault("leaderboard",{}).setdefault(str(self.user_id),{"floor":floor,"kills":0}); lb["floor"]=p["floor"]; lb["kills"]+=1; save_item_data(); await self.refresh(interaction,f"**{boss[0]} DEFEATED!**\n**{loot['name']}** — {loot['rarity']}\nAdvanced to Floor **{p['floor']}**.")
    else:
      if p.get("death_protection",0)>0:p["death_protection"]-=1; msg=" Death Protection saved your run."
      else:p["position"]=0; p["explored"]=False; msg=f" **{boss[0]} defeated you.** You restart Floor {floor}."
      save_item_data(); await self.refresh(interaction,msg)

  @discord.ui.button(label="Equipment",style=discord.ButtonStyle.secondary,row=2)
  async def equipment_btn(self,interaction,button):
    if not await self.check(interaction): return
    from .equipment import equipment_lines, combat_stats
    stats=combat_stats(self.guild_id,self.user_id)
    await interaction.edit_original_response(content=" **EQUIPMENT**\n\n"+equipment_lines(self.guild_id,self.user_id)+"\n\n**Combat Stats**\n"+" • ".join(f"{label}: **{value}**" for label,value in qualitative_stats(stats))+"\n\nUse `/equip` with autocomplete to equip an owned weapon or armor.",view=self)

  @discord.ui.button(label="Consumables",style=discord.ButtonStyle.secondary,row=2)
  async def consumables_btn(self,interaction,button):
    if not await self.check(interaction): return
    inv=item_state(self.guild_id).get("inventories",{}).get(str(self.user_id),[])
    consumables=[x.get("item",x) for x in inv if x.get("item",x).get("category")=="item"]
    lines=[f"• **{x.get('name','Unknown')}** — {x.get('rarity','Common')}" for x in consumables[:25]]
    await interaction.edit_original_response(content=" **CONSUMABLES**\n\n"+("\n".join(lines) if lines else "*You have no consumables.*")+"\n\nUse `/use-item` with autocomplete to use one.",view=self)

  @discord.ui.button(label="Leaderboard",style=discord.ButtonStyle.secondary,row=2)
  async def leaderboard_btn(self,interaction,button):
    if not await self.check(interaction): return
    d,_=dstate(self.guild_id,self.user_id); rows=leaderboard(d,self.guild_id)
    text=" **DUNGEON LEADERBOARD**\n\n"+("\n".join(f"**{i}.** <@{uid}> — Floor **{v['floor']}** • Bosses **{v['kills']}**" for i,(uid,v) in enumerate(rows,1)) if rows else "*No dungeon runs yet.*")
    await interaction.edit_original_response(content=text,view=self)

  @discord.ui.button(label="Main",style=discord.ButtonStyle.success,row=2)
  async def main_btn(self,interaction,button):
    if not await self.check(interaction): return
    from .main_ui import MainView
    await interaction.edit_original_response(content=" **ANONYMOUS RPG**\n\nChoose a section below.",view=MainView(self.guild_id,self.user_id))

@DUNGEON_GROUP.command(name="open",description="Open the compact dungeon UI; actions edit the same message.")
async def dungeon(interaction:discord.Interaction):
  if interaction.guild is None:return await interaction.response.send_message("Server only.",ephemeral=True)
  await interaction.response.defer(ephemeral=True)
  d,p=dstate(interaction.guild.id,interaction.user.id); floor=p["floor"]
  if floor in d["locked"]: return await interaction.edit_original_response(content=" This floor is locked by an admin.",view=None)
  terrain,boss,chest,event=floor_info(floor); stats=combat_stats(interaction.guild.id,interaction.user.id)
  stat_text=" • ".join(f"{label}: **{value}**" for label,value in qualitative_stats(stats))
  text=f" **DUNGEON — FLOOR {floor}/{MAX_FLOOR}**\n\n**Area:** {terrain} • **Boss:** {boss[0]}\n**Chest:** {' Present' if chest else 'None'} • **Event:** {event.replace('_',' ').title()}\n**Checkpoint:** {p.get('checkpoint',1)} • **Progress:** {p.get('position',0)}/3\n{stat_text}"
  await interaction.edit_original_response(content=text,view=DungeonView(interaction.guild.id,interaction.user.id))

@DUNGEON_GROUP.command(name="move",description="Move through the current dungeon floor.")
async def dungeon_move(interaction:discord.Interaction):
  if interaction.guild is None:return await interaction.response.send_message("Server only.",ephemeral=True)
  d,p=dstate(interaction.guild.id,interaction.user.id); floor=p["floor"]
  if floor in d["locked"]: return await interaction.response.send_message(" This floor is locked.",ephemeral=True)
  p["position"]+=1; save_item_data()
  if p["position"]>=3: p["explored"]=True; result="You reached the boss chamber."
  else: result=f"You move deeper. Progress: {p['position']}/3."
  await interaction.response.send_message(f" {result}\n",ephemeral=True)

@DUNGEON_GROUP.command(name="event",description="Resolve the current floor's event.")
async def dungeon_event(interaction:discord.Interaction):
  if interaction.guild is None:return await interaction.response.send_message("Server only.",ephemeral=True)
  d,p=dstate(interaction.guild.id,interaction.user.id); floor=p["floor"]
  if p.get("event_done"): return await interaction.response.send_message("The event on this floor has already been resolved.",ephemeral=True)
  _,_,_,event=floor_info(floor); msg=""
  if event=="merchant": msg="merchant A wandering merchant offers a temporary 15% discount on the shop for this visit."
  elif event=="shrine": msg=" A shrine reveals a safe route through the floor."
  elif event=="healing_pool": msg=" A hidden pool restores your vitality for the upcoming battle."
  elif event=="trap": msg=" A trap slows your progress, but you push onward."
  elif event=="npc": msg=" An explorer gives you a clue about the boss."
  elif event=="puzzle": msg=" You solve a strange mechanism and gain a hidden **chest bonus** on this floor."
  elif event=="secret_room":
    loot=make_loot(floor,bonus=True); add_item(interaction.guild.id,interaction.user.id,loot,held=True); msg=f"Secret room! You found **{loot['name']}** — {loot['rarity']}."
  else: msg=" An ambush! You survive and learn the boss is unusually aggressive."
  p["event_done"]=True; save_item_data(); await interaction.response.send_message(msg+f"\n",ephemeral=True)

@DUNGEON_GROUP.command(name="fight",description="Fight the floor boss using your equipped gear.")
async def dungeon_fight(interaction:discord.Interaction):
  if interaction.guild is None:return await interaction.response.send_message("Server only.",ephemeral=True)
  d,p=dstate(interaction.guild.id,interaction.user.id); floor=p["floor"]
  if floor in d["locked"]: return await interaction.response.send_message(" This floor is locked.",ephemeral=True)
  if p.get("position",0)<3:return await interaction.response.send_message(" Reach the boss chamber first with `/dungeon-move`.",ephemeral=True)
  _,boss,chest,event=floor_info(floor); stats=combat_stats(interaction.guild.id,interaction.user.id); won,rounds=combat_result(floor,boss[1],stats)
  if won:
    loot=make_loot(floor,bonus=True,boss_key=boss[1]); add_item(interaction.guild.id,interaction.user.id,loot,held=True)
    p["floor"]=min(MAX_FLOOR,floor+1); p["position"]=0; p["explored"]=False; p["event_done"]=False
    if p["floor"]%10==1: p["checkpoint"]=p["floor"]
    lb=d.setdefault("leaderboard",{}).setdefault(str(interaction.user.id),{"floor":floor,"kills":0}); lb["floor"]=p["floor"]; lb["kills"]+=1
    save_item_data(); await interaction.response.send_message(f"**{boss[0]} DEFEATED!**\nCombat rounds: **{rounds}**\nYour equipment stats determined the fight.\n**Boss-specific loot:** {loot['name']} — **{loot['rarity']}**\nAdvanced to **Floor {p['floor']}**\nCheckpoint: **Floor {p['checkpoint']}**\n",ephemeral=True)
  else:
    if p.get("death_protection",0)>0:
      p["death_protection"]-=1; msg=" Your Death Protection saved your run. You remain on the current floor."
    else:
      p["position"]=0; p["explored"]=False; msg=f" **{boss[0]} defeated you.** You restart Floor {floor}."
    save_item_data()
    await interaction.response.send_message(msg,ephemeral=True)

@DUNGEON_GROUP.command(name="search",description="Search the current floor for its chest.")
async def dungeon_search(interaction:discord.Interaction):
  if interaction.guild is None:return await interaction.response.send_message("Server only.",ephemeral=True)
  d,p=dstate(interaction.guild.id,interaction.user.id); floor=p["floor"]
  _,_,chest,event=floor_info(floor)
  if not chest or p.get("chest_found_floor")==floor:return await interaction.response.send_message(" You find no chest here.",ephemeral=True)
  p["chest_found_floor"]=floor; loot=make_loot(floor,bonus=True); add_item(interaction.guild.id,interaction.user.id,loot,held=True); save_item_data()
  await interaction.response.send_message(f"**CHEST FOUND!** {loot['name']} — **{loot['rarity']}**\n",ephemeral=True)

@DUNGEON_GROUP.command(name="leaderboard",description="View the deepest dungeon runs on this server.")
async def dungeon_leaderboard(interaction:discord.Interaction):
  if interaction.guild is None:return await interaction.response.send_message("Server only.",ephemeral=True)
  d,_=dstate(interaction.guild.id,interaction.user.id); rows=leaderboard(d,interaction.guild.id)
  if not rows:return await interaction.response.send_message(" No dungeon runs yet.",ephemeral=True)
  text=" **DUNGEON LEADERBOARD**\n\n"+"\n".join(f"**{i}.** <@{uid}> — Floor **{v['floor']}** • Bosses **{v['kills']}**" for i,(uid,v) in enumerate(rows,1))
  await interaction.response.send_message(text,ephemeral=True)

@ADMIN_DUNGEON_GROUP.command(name="lock-floor",description="Admin: lock a dungeon floor.")
@app_commands.describe(floor="Floor number to lock.")
async def lock_floor(interaction:discord.Interaction,floor:int):
  from ..state import is_staff
  if interaction.guild is None or not is_staff(interaction):return await interaction.response.send_message("GM/admin only.",ephemeral=True)
  if floor<1 or floor>MAX_FLOOR:return await interaction.response.send_message(f"Floor must be 1-{MAX_FLOOR}.",ephemeral=True)
  d=item_state(interaction.guild.id).setdefault("dungeon",{"players":{},"locked":[]}); d.setdefault("locked",[])
  if floor not in d["locked"]:d["locked"].append(floor)
  save_item_data(); await interaction.response.send_message(f" Floor **{floor}** locked.",ephemeral=True)

@ADMIN_DUNGEON_GROUP.command(name="unlock-floor",description="Admin: unlock a dungeon floor.")
@app_commands.describe(floor="Floor number to unlock.")
async def unlock_floor(interaction:discord.Interaction,floor:int):
  from ..state import is_staff
  if interaction.guild is None or not is_staff(interaction):return await interaction.response.send_message("GM/admin only.",ephemeral=True)
  d=item_state(interaction.guild.id).setdefault("dungeon",{"players":{},"locked":[]}); d.setdefault("locked",[])
  if floor in d["locked"]:d["locked"].remove(floor)
  save_item_data(); await interaction.response.send_message(f" Floor **{floor}** unlocked.",ephemeral=True)

COMMANDS=[dungeon,dungeon_move,dungeon_event,dungeon_fight,dungeon_search,dungeon_leaderboard,lock_floor,unlock_floor]
def register(bot):
  bot.tree.add_command(DUNGEON_GROUP)