"""
Human survival / society simulation with a rule-based AI (no training).

Humans move freely over an island with grass (food), lakes (drink) and forests
(wood). They have needs, a personality (aggression), relationships, clans and tools.

  * Kindness (sharing / trading) builds trust: trusted people are rarely attacked
    and are invited into clans. Attacks destroy trust.
  * Clans fight together: when a member fights or is attacked, nearby members join in.
    Clanmates never attack each other and share their tool knowledge.
  * Tools advance (none -> stone -> bronze -> iron) by crafting with wood/food.
    Better tools gather more food and deal more damage.

  python life.py [--humans 50] [--aggression 0.5] [--rows 24]

Controls:  click/Tab select   0 control selected <-> ALL
           WASD walk  R rest  E eat  F drink  G gather  C craft  H share  T trade  X attack
           space pause  N step  +/- speed  Esc deselect  Q quit
           M  admin panel (right-click sets a target)
"""
import argparse
import ctypes
import math

import cv2
import numpy as np

GRASS, WATER, FOREST = 0, 1, 2
SPEED = 0.09                      # tiles per frame
REACH = 1.0                       # attack / share / trade distance (tiles)
COOLDOWN = 18                     # frames between actions
RESPAWN = 150                     # frames until a dead human is replaced
TOOLS = ["bare hands", "stone", "bronze", "iron"]
TOOL_COST = [(3, 0), (5, 2), (8, 4)]          # (wood, food) to reach the next level
TRUST_TO_JOIN = 1.5               # trust needed before clan invitations
GUN_CLAN_SIZE = 10                # clans this big get guns
GUN_RANGE = 4.0                   # tiles
GUN_DAMAGE = 35
TITLE = "WARLORDS"                # title of the clan with the most kills
# gun upgrades: name -> (range tiles, damage, cooldown frames, targets hit)
WEAPONS = [("pistol", 4.0, 35, 18, 1), ("shotgun", 2.5, 30, 22, 3),
           ("machine gun", 4.5, 14, 5, 1), ("sniper", 9.0, 75, 45, 1)]
WEAPON_COST = (6, 4)              # wood, food to upgrade a pistol
MISSILE_CLAN = 15                 # clans this big launch missiles
NUKE_CLAN = 25                    # clans this big launch nukes
MISSILE_COOLDOWN, NUKE_COOLDOWN = 240, 1500
BIRTH_CHANCE = 0.012              # per frame, for a healthy fed adult with spare food
REBEL_CLAN = 20                   # clans this big can spawn rebel factions of 2-4
FORT_CLAN = 8                     # clans this big build fortresses
FORT_COST = [10, 25, 50]          # wood to build / upgrade to level 1, 2, 3
FORT_RADIUS = [0, 2.0, 2.6, 3.2]  # tiles, by level
FORT_ARMOR = [0, 0.3, 0.5, 0.7]   # damage reduction for defenders inside
ADULT_AGE = 400                   # frames before someone can have children
LIFESPAN = (9000, 1500)           # mean, spread (frames) before dying of old age
ETHNIC_COLORS = [(60, 95, 150), (150, 190, 235), (35, 45, 75), (90, 150, 205)]   # BGR skin tones
ETHNIC_NAMES = ["A", "B", "C", "D"]

STATE_COLOR = {"raiding": (0, 60, 255), "fighting": (40, 40, 235), "defending clan": (40, 40, 235), "hunting": (60, 110, 240),
               "sharing": (40, 220, 230), "trading": (230, 210, 40), "drinking": (235, 160, 60),
               "eating": (60, 200, 90), "crafting": (200, 80, 200)}
CLAN_COLORS = [(0, 215, 255), (255, 80, 200), (80, 255, 120), (255, 160, 40), (60, 60, 255),
               (255, 255, 80), (160, 100, 255), (40, 180, 255), (200, 255, 200), (130, 0, 200)]


def screen_size():
    try:
        u = ctypes.windll.user32
        u.SetProcessDPIAware()
        return u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    except Exception:
        return 1600, 900


# ---------------------------------------------------------------------------
class World:
    def __init__(self, n, cols, rows, aggression, seed=0, discrimination=0.5):
        self.n, self.W, self.H = n, cols, rows
        self.rng = np.random.default_rng(seed)
        self._terrain()
        self.pos = np.zeros((n, 2))
        self.heading = self.rng.uniform(0, 2 * np.pi, n)
        self.hunger, self.thirst = np.zeros(n), np.zeros(n)
        self.health = np.full(n, 100.0)
        self.food_inv, self.wood_inv = np.zeros(n), np.zeros(n)
        self.tool = np.zeros(n, int)
        self.aggression = np.clip(self.rng.normal(aggression, 0.25, n), 0, 1)
        self.trust = np.zeros((n, n))             # trust[i, j]: how much i likes j
        self.clan = np.full(n, -1)
        self.clan_enemy = {}                      # clan -> (enemy, frames left)
        self.raids = {}                           # attacking clan -> (target clan, frames left)
        self.clan_kills = {}                      # clan -> kills over the whole simulation
        self.warlord = None                       # clan holding the title
        self.gun_clans = set()                    # clans armed with guns
        self.admin_guns = np.zeros(n, bool)       # guns handed out by the admin
        self.shots = {}                           # shooter -> (victim, frames left)
        self.weapon = np.zeros(n, int)            # index into WEAPONS
        self.ethnicity = self.rng.integers(0, len(ETHNIC_COLORS), n)
        self.discrimination = discrimination      # how much prejudice matters (admin P)
        self.prejudice = np.clip(self.rng.normal(0.5, 0.25, n), 0, 1)
        self.strength = np.clip(self.rng.normal(1, 0.1, n), 0.5, 3)
        self.speed_mult = np.clip(self.rng.normal(1, 0.1, n), 0.5, 2.5)
        self.mutant = np.zeros(n, bool)
        self.missiles = []                        # flying missiles / nukes
        self.blasts = []                          # explosions being drawn
        self.clan_cd = {}                         # clan -> [missile cooldown, nuke cooldown]
        self.age = self.rng.uniform(0, 3000, n)
        self.parent = np.full(n, -1)
        self.invincible = np.zeros(n, bool)
        self.forts = {}                           # clan -> {"pos", "level", "wood"}
        self.lifespan = self.rng.normal(*LIFESPAN, n)
        self.next_clan = 0
        self.alive = np.ones(n, bool)
        self.dead_for = np.zeros(n, int)
        self.cooldown = np.zeros(n, int)
        self.state = ["" for _ in range(n)]
        self.wander = [None] * n
        self.hit_flash = np.zeros(n, int)
        self.strike = {}
        self.t = 0
        self.stats = dict.fromkeys(["attacks", "kills", "starved", "shares", "trades", "born",
                                    "joined", "crafted", "missiles", "nukes", "mutants", "old age",
                                    "rebellions", "forts built"], 0)
        for i in range(n):
            self._spawn(i)
        self.age[:] = self.rng.uniform(ADULT_AGE, 3000, n)      # founders start as adults

    # -- population storage (grows without limit) --------------------------------
    GROW_FIELDS = [("heading", 0.0), ("hunger", 0.0), ("thirst", 0.0), ("health", 100.0),
                   ("food_inv", 0.0), ("wood_inv", 0.0), ("tool", 0), ("aggression", 0.5),
                   ("clan", -1), ("alive", False), ("dead_for", 0), ("cooldown", 0),
                   ("hit_flash", 0), ("admin_guns", False), ("weapon", 0), ("ethnicity", 0),
                   ("prejudice", 0.5), ("strength", 1.0), ("speed_mult", 1.0), ("mutant", False),
                   ("age", 0.0), ("lifespan", 9000.0), ("parent", -1), ("invincible", False)]

    def _grow(self, extra):
        """Add storage for `extra` more people."""
        for name, default in self.GROW_FIELDS:
            a = getattr(self, name)
            setattr(self, name, np.concatenate([a, np.full(extra, default, a.dtype)]))
        self.pos = np.vstack([self.pos, np.zeros((extra, 2))])
        t = np.zeros((self.n + extra, self.n + extra))
        t[:self.n, :self.n] = self.trust
        self.trust = t
        self.state += ["dead"] * extra
        self.wander += [None] * extra
        self.dead_for[self.n:] = 10 ** 6
        self.n += extra

    def free_slot(self):
        dead = np.where(~self.alive & (self.dead_for > 30))[0]
        if not len(dead):
            self._grow(max(16, self.n // 4))
            dead = np.where(~self.alive & (self.dead_for > 30))[0]
        return int(dead[0])

    # -- map -------------------------------------------------------------------
    def _terrain(self):
        W, H, r = self.W, self.H, self.rng
        ter = np.zeros((H, W), np.int8)
        yy, xx = np.mgrid[0:H, 0:W] + 0.5
        area = W * H / 576
        for kind, count in ((WATER, int(4 * area) + 1), (FOREST, int(5 * area) + 1)):
            for _ in range(count):
                cx, cy, rad = r.uniform(0, W), r.uniform(0, H), r.uniform(1.8, 3.5)
                blob = (xx - cx) ** 2 + (yy - cy) ** 2 < rad ** 2
                ter[blob & (ter == GRASS)] = kind
        self.terrain = ter
        self.food = r.uniform(0, 3, (H, W)) * (ter == GRASS)
        self.water_pts = np.argwhere(ter == WATER)[:, ::-1] + 0.5

    def tile(self, p):
        return int(np.clip(p[0], 0, self.W - 1e-6)), int(np.clip(p[1], 0, self.H - 1e-6))

    def is_water(self, p):
        if not (0 <= p[0] < self.W and 0 <= p[1] < self.H):
            return True
        x, y = self.tile(p)
        return self.terrain[y, x] == WATER

    def near_water(self, i):
        return len(self.water_pts) and np.min(np.hypot(*(self.water_pts - self.pos[i]).T)) < 1.15

    def _spawn(self, i):
        while True:
            p = self.rng.uniform(0.5, [self.W - 0.5, self.H - 0.5])
            if not self.is_water(p):
                break
        self.pos[i] = p
        self.hunger[i], self.thirst[i], self.health[i] = 10, 10, 100
        self.food_inv[i] = self.wood_inv[i] = 0
        self.tool[i] = 0
        self.weapon[i] = 0
        self.mutant[i] = False
        self.trust[i, :] = self.trust[:, i] = 0
        self.clan[i] = -1
        self.alive[i] = True
        self.dead_for[i] = 0
        self.wander[i] = None
        self.age[i] = 0
        self.lifespan[i] = self.rng.normal(*LIFESPAN)
        self.parent[i] = -1
        self.invincible[i] = False

    # -- relationships ------------------------------------------------------------
    def is_child(self, i):
        return self.age[i] < ADULT_AGE

    def family(self, i, j):
        return self.parent[i] == j or self.parent[j] == i or \
            (self.parent[i] >= 0 and self.parent[i] == self.parent[j])

    def friendly(self, i, j):
        return (self.clan[i] >= 0 and self.clan[i] == self.clan[j]) or self.family(i, j)

    def bias(self, i, j):
        """0..1: how much i is prejudiced against j (0 if same ethnicity)."""
        if self.ethnicity[i] == self.ethnicity[j]:
            return 0.0
        return float(self.prejudice[i] * self.discrimination)

    def kindness(self, giver, receiver, amount):
        """Kind acts build trust and can lead to joining / forming a clan."""
        amount *= 1 - 0.8 * self.bias(receiver, giver)             # prejudice resists kindness
        self.trust[receiver, giver] += amount
        self.trust[giver, receiver] += amount * 0.5
        t = min(self.trust[receiver, giver], self.trust[giver, receiver] * 2)
        prejudiced = self.rng.random() < max(self.bias(giver, receiver), self.bias(receiver, giver))
        if t >= TRUST_TO_JOIN and not self.friendly(giver, receiver) and not prejudiced \
                and self.rng.random() < 0.4 * t / TRUST_TO_JOIN:
            ci, cj = self.clan[giver], self.clan[receiver]
            if ci < 0 and cj < 0:                       # found a new clan
                self.clan[giver] = self.clan[receiver] = self.next_clan
                self.next_clan += 1
            elif cj < 0:
                self.clan[receiver] = ci
            elif ci < 0:
                self.clan[giver] = cj
            else:
                return
            self.stats["joined"] += 1
            self._share_knowledge(self.clan[giver])

    def _share_knowledge(self, c):
        members = np.where(self.alive & (self.clan == c))[0]
        if len(members):                # clanmates learn the best tool anyone knows
            self.tool[members] = np.maximum(self.tool[members], self.tool[members].max() - 1)

    def clan_members(self, c):
        return np.where(self.alive & (self.clan == c))[0]

    def leader(self, c):
        m = self.clan_members(c)
        if not len(m):
            return None
        adults = m[self.age[m] >= ADULT_AGE]
        pool = adults if len(adults) else m              # children lead only if no adults remain
        score = 10 * self.tool[pool] + self.age[pool] / 500 + self.health[pool] / 50
        return int(pool[np.argmax(score)])

    def clan_centre(self, c):
        m = self.clan_members(c)
        return self.pos[m].mean(0) if len(m) else None

    def _update_clans(self):
        sizes = {c: len(self.clan_members(c)) for c in set(self.clan[self.alive]) if c >= 0}
        self.gun_clans = {c for c, n in sizes.items() if n >= GUN_CLAN_SIZE}
        self.raids = {a: v for a, v in self.raids.items() if sizes.get(v[0]) and sizes.get(a)}
        living = {c: k for c, k in self.clan_kills.items() if sizes.get(c)}
        best = max(living, key=living.get) if living else None
        self.warlord = best if best is not None and living[best] >= 5 else None
        if self.warlord is not None:                 # every other clan turns on the warlords
            for c in sizes:
                if c != self.warlord and sizes[c] >= 2:
                    self.raids[c] = (self.warlord, 400)

    def _rebellions(self):
        """Inside giant clans, a faction of 2-4 restless members breaks away and attacks."""
        for c in [c for c in set(self.clan[self.alive]) if c >= 0]:
            m = self.clan_members(c)
            m = m[~np.array([self.is_child(k) for k in m], bool)]
            if len(m) < REBEL_CLAN or self.rng.random() > 0.35:
                continue
            ring = m[np.argmax(self.aggression[m] + self.rng.random(len(m)) * 0.3)]
            d = np.hypot(*(self.pos[m] - self.pos[ring]).T)
            faction = m[np.argsort(d)[:self.rng.integers(2, 5)]]
            new = self.next_clan
            self.next_clan += 1
            self.clan[faction] = new
            for k in faction:                                 # old loyalties sour
                self.trust[k, m] = np.minimum(self.trust[k, m], -1)
                self.trust[m, k] = np.minimum(self.trust[m, k], -1)
            self.raids[new] = (c, 900)
            self.stats["rebellions"] += 1

    def build(self, i):
        """Bring wood to the clan fortress (founding it at the clan centre if needed)."""
        c = self.clan[i]
        if c < 0 or self.wood_inv[i] < 1 or not self._ready(i):
            return
        f = self.forts.get(c)
        if f is None:
            f = self.forts[c] = {"pos": self.pos[i].copy(), "level": 0, "wood": 0.0}
        give = self.wood_inv[i]
        self.wood_inv[i] = 0
        f["wood"] += give
        while f["level"] < 3 and f["wood"] >= FORT_COST[f["level"]]:
            f["wood"] -= FORT_COST[f["level"]]
            f["level"] += 1
            self.stats["forts built"] += 1

    def _plan_raids(self):
        """Aggressive clans with 3+ members pick another clan to raid."""
        clans = [c for c in set(self.clan[self.alive]) if c >= 0]
        self.raids = {a: (b, f - 1) for a, (b, f) in self.raids.items()
                      if f > 1 and len(self.clan_members(a)) >= 2 and len(self.clan_members(b))}
        for c in clans:
            m = self.clan_members(c)
            if c in self.raids or len(m) < 3:
                continue
            if self.rng.random() < 0.25 + self.aggression[m].mean():
                targets = [d for d in clans if d != c]
                if targets:
                    me = self.clan_centre(c)
                    d = min(targets, key=lambda t: np.hypot(*(self.clan_centre(t) - me)))
                    self.raids[c] = (d, 900)

    # -- actions -----------------------------------------------------------------
    def move(self, i, direction):
        d = np.asarray(direction, float)
        if not d.any():
            return
        d /= np.linalg.norm(d)
        base = math.atan2(d[1], d[0])
        for turn in (0, 0.6, -0.6, 1.2, -1.2, 1.9, -1.9):
            a = base + turn
            step = np.array([math.cos(a), math.sin(a)]) * SPEED * self.speed_mult[i]
            if not self.is_water(self.pos[i] + step * 4):
                self.pos[i] += step
                self.heading[i] = a
                return

    def _ready(self, i):
        if self.cooldown[i] > 0:
            return False
        self.cooldown[i] = COOLDOWN
        return True

    def eat(self, i):
        if not self._ready(i):
            return
        x, y = self.tile(self.pos[i])
        if self.food_inv[i] >= 1:
            self.food_inv[i] -= 1
            self.hunger[i] = max(0, self.hunger[i] - 35)
        elif self.food[y, x] >= 1:
            self.food[y, x] -= 1
            self.hunger[i] = max(0, self.hunger[i] - 30)

    def drink(self, i):
        if self.near_water(i) and self._ready(i):
            self.thirst[i] = max(0, self.thirst[i] - 45)

    def gather(self, i):
        if not self._ready(i):
            return
        x, y = self.tile(self.pos[i])
        if self.terrain[y, x] == FOREST:
            self.wood_inv[i] += 1 + (self.tool[i] >= 2)
        elif self.food[y, x] >= 1:
            self.food[y, x] -= 1
            self.food_inv[i] += 1 + self.tool[i] * 0.5          # better tools, bigger harvest

    def craft(self, i):
        lvl = self.tool[i]
        if lvl >= len(TOOL_COST) or not self._ready(i):
            return
        wood, food = TOOL_COST[lvl]
        if self.wood_inv[i] >= wood and self.food_inv[i] >= food:
            self.wood_inv[i] -= wood
            self.food_inv[i] -= food
            self.tool[i] += 1
            self.stats["crafted"] += 1
            if self.clan[i] >= 0:
                self._share_knowledge(self.clan[i])

    def can_craft(self, i):
        lvl = self.tool[i]
        if lvl >= len(TOOL_COST):
            return False
        wood, food = TOOL_COST[lvl]
        return self.wood_inv[i] >= wood and self.food_inv[i] >= food

    def has_gun(self, i):
        if self.age[i] < ADULT_AGE:
            return False
        return bool(self.admin_guns[i]) or (self.clan[i] >= 0 and self.clan[i] in self.gun_clans)

    def reach(self, i):
        return WEAPONS[self.weapon[i]][1] if self.has_gun(i) else REACH

    def has_gun_mask(self):
        return np.array([self.alive[i] and self.has_gun(i) for i in range(self.n)])

    def can_upgrade_weapon(self, i):
        return self.has_gun(i) and self.weapon[i] == 0 and \
            self.wood_inv[i] >= WEAPON_COST[0] and self.food_inv[i] >= WEAPON_COST[1]

    def upgrade_weapon(self, i, kind=None):
        """Pistol -> shotgun / machine gun / sniper (chosen by personality)."""
        if kind is None:
            if not self.can_upgrade_weapon(i) or not self._ready(i):
                return
            self.wood_inv[i] -= WEAPON_COST[0]
            self.food_inv[i] -= WEAPON_COST[1]
            a = self.aggression[i]
            kind = 2 if a > 0.7 else 3 if a < 0.4 else 1
        self.weapon[i] = kind
        self.stats["crafted"] += 1

    def nearest_person(self, i, max_dist=REACH, enemies_only=False):
        others = np.where(self.alive)[0]
        others = others[others != i]
        if enemies_only:
            others = others[[not self.friendly(i, j) and not self.is_child(j) for j in others]]
        if not len(others):
            return None, None
        d = np.hypot(*(self.pos[others] - self.pos[i]).T)
        k = np.argmin(d)
        return (others[k], d[k]) if d[k] <= max_dist else (None, d[k])

    def attack(self, i, j=None):
        if self.is_child(i):
            return
        if j is None:
            j, _ = self.nearest_person(i, self.reach(i), enemies_only=True)
        if j is None or self.is_child(j) or self.friendly(i, j) \
                or np.hypot(*(self.pos[j] - self.pos[i])) > self.reach(i) or not self._ready(i):
            return
        victims = [j]
        if self.has_gun(i):
            _, _, dmg, cd, hits = WEAPONS[self.weapon[i]]
            self.cooldown[i] = cd
            if hits > 1:                                   # shotgun spread
                near = [k for k in np.where(self.alive)[0] if k not in (i, j)
                        and not self.friendly(i, k) and not self.is_child(k)
                        and np.hypot(*(self.pos[k] - self.pos[j])) < 1.2]
                victims += near[:hits - 1]
            for v in victims:
                self.shots[(i, v)] = (v, 5)
        else:
            dmg = 15 + 8 * self.tool[i]
        self.strike[i] = (j, 6)
        self.heading[i] = math.atan2(*(self.pos[j] - self.pos[i])[::-1])
        self.stats["attacks"] += 1
        for v in victims:
            self.hurt(v, dmg * self.strength[i], i)

    def hurt(self, j, dmg, i=None, launcher_clan=-1):
        """Damage j. i = attacker (or None for missiles/nukes of launcher_clan)."""
        if not self.alive[j] or self.invincible[j]:
            return
        f = self.forts.get(self.clan[j])
        if f and np.hypot(*(self.pos[j] - f["pos"])) <= FORT_RADIUS[f["level"]]:
            dmg *= 1 - FORT_ARMOR[f["level"]]                # defenders behind walls
        self.health[j] -= dmg
        self.hit_flash[j] = 6
        killer_clan = self.clan[i] if i is not None else launcher_clan
        if i is not None:
            self.trust[j, i] -= 4                                 # attacks destroy trust
            self.trust[i, j] -= 1
            for c, enemy in ((self.clan[j], i), (self.clan[i], j)):    # clans rally
                if c >= 0:
                    self.clan_enemy[c] = (enemy, 240)
            push = self.pos[j] - self.pos[i]
            push = push / (np.linalg.norm(push) + 1e-9) * 0.25
            if not self.is_water(self.pos[j] + push):
                self.pos[j] += push
        elif self.clan[j] >= 0 and launcher_clan >= 0:            # bombed clans go to war
            self.raids[self.clan[j]] = (launcher_clan, 900)
        if self.health[j] <= 0:
            if i is not None:
                self.food_inv[i] += self.food_inv[j]
                self.wood_inv[i] += self.wood_inv[j]
            self._die(j)
            self.stats["kills"] += 1
            if killer_clan >= 0:
                self.clan_kills[killer_clan] = self.clan_kills.get(killer_clan, 0) + 1

    def share(self, i):
        j, _ = self.nearest_person(i)
        if j is not None and self.food_inv[i] >= 1 and self._ready(i):
            self.food_inv[i] -= 1
            self.food_inv[j] += 1
            self.stats["shares"] += 1
            self.kindness(i, j, 1.5)

    def trade(self, i):
        j, _ = self.nearest_person(i)
        if j is None or not self._ready(i):
            return
        mine = "food_inv" if self.food_inv[i] > self.wood_inv[i] else "wood_inv"
        theirs = "wood_inv" if mine == "food_inv" else "food_inv"
        a, b = getattr(self, mine), getattr(self, theirs)
        if a[i] >= 1 and b[j] >= 1:
            a[i] -= 1; a[j] += 1
            b[j] -= 1; b[i] += 1
            self.stats["trades"] += 1
            self.kindness(i, j, 0.8)
            self.kindness(j, i, 0.8)
            if self.tool[i] != self.tool[j] and self.rng.random() < 0.2:   # ideas spread by trade
                lo = i if self.tool[i] < self.tool[j] else j
                self.tool[lo] += 1

    # -- admin ---------------------------------------------------------------------
    def admin_new_clan(self, i, radius=3.0):
        near = np.where(self.alive & (np.hypot(*(self.pos - self.pos[i]).T) <= radius))[0]
        c = self.next_clan
        self.next_clan += 1
        self.clan[near] = c
        return c, len(near)

    def admin_join(self, i, j):
        if self.clan[j] < 0:
            self.clan[j] = self.next_clan
            self.next_clan += 1
        self.clan[i] = self.clan[j]

    def admin_spawn(self, k, near=None):
        dead = [self.free_slot() for _ in range(k)]
        for d in dead:
            self._spawn(d)
            if near is not None:
                p = np.clip(near + self.rng.normal(0, 1.5, 2), 0.5, [self.W - 0.5, self.H - 0.5])
                if not self.is_water(p):
                    self.pos[d] = p
        return len(dead)

    def world_war(self, armies=4):
        """Split everyone into rival armies by location, arm them, and start raids."""
        idx = np.where(self.alive)[0]
        if len(idx) < armies:
            return 0
        cent = self.pos[self.rng.choice(idx, armies, replace=False)].copy()
        for _ in range(10):                                  # k-means on positions
            lab = np.argmin(((self.pos[idx, None] - cent[None]) ** 2).sum(-1), 1)
            for k in range(armies):
                if (lab == k).any():
                    cent[k] = self.pos[idx[lab == k]].mean(0)
        ids = []
        for k in range(armies):
            c = self.next_clan
            self.next_clan += 1
            self.clan[idx[lab == k]] = c
            ids.append((c, cent[k]))
        adults = idx[self.age[idx] >= ADULT_AGE]
        self.admin_guns[adults] = True
        self.aggression[adults] = np.maximum(self.aggression[adults], 0.7)
        self.clan_kills.clear()
        self.warlord = None
        self.raids.clear()
        for c, p in ids:                                     # raid the nearest rival army
            other = min((o for o in ids if o[0] != c), key=lambda o: np.hypot(*(o[1] - p)))
            self.raids[c] = (other[0], 1500)
        self._update_clans()
        return armies

    # -- missiles & nukes ------------------------------------------------------------
    def launch(self, clan, target, kind="missile"):
        start = self.clan_centre(clan) if clan >= 0 else None
        if start is None:
            start = np.array([target[0], 0.0])
        self.missiles.append({"pos": start.astype(float).copy(), "start": start.copy(),
                              "target": np.asarray(target, float), "clan": clan, "kind": kind})
        self.stats["missiles" if kind == "missile" else "nukes"] += 1

    def _explode(self, m):
        nuke = m["kind"] == "nuke"
        radius, dmg = (6.0, 999) if nuke else (2.0, 60)
        d = np.hypot(*(self.pos - m["target"]).T)
        for j in np.where(self.alive & (d <= radius))[0]:
            if self.clan[j] != m["clan"] or m["clan"] < 0:
                self.hurt(j, dmg * (1 - 0.5 * d[j] / radius), None, m["clan"])
        for c, f in list(self.forts.items()):                 # blasts wreck fortresses
            if np.hypot(*(f["pos"] - m["target"])) <= radius + 1:
                f["level"] -= 3 if nuke else 1
                if f["level"] <= 0:
                    del self.forts[c]
        if nuke:                                            # fallout: scorch the land
            yy, xx = np.mgrid[0:self.H, 0:self.W] + 0.5
            burn = np.hypot(xx - m["target"][0], yy - m["target"][1]) <= radius
            self.food[burn] = 0
        self.blasts.append([m["target"].copy(), radius, 45 if nuke else 14, m["kind"]])

    def _weapons_of_mass_destruction(self):
        for c, (tc, _) in list(self.raids.items()):
            size = len(self.clan_members(c))
            tgt = self.clan_centre(tc)
            if size < MISSILE_CLAN or tgt is None:
                continue
            cd = self.clan_cd.setdefault(c, [40, 250])        # first strike comes quickly
            if size >= NUKE_CLAN and cd[1] <= 0:
                self.launch(c, tgt, "nuke")
                cd[1] = NUKE_COOLDOWN
            elif cd[0] <= 0:
                self.launch(c, tgt + self.rng.normal(0, 0.7, 2), "missile")
                cd[0] = MISSILE_COOLDOWN
        for cd in self.clan_cd.values():
            cd[0] -= 1
            cd[1] -= 1
        flying = []
        for m in self.missiles:
            step = m["target"] - m["pos"]
            dist = np.linalg.norm(step)
            speed = 0.25 if m["kind"] == "nuke" else 0.4
            if dist <= speed:
                self._explode(m)
            else:
                m["pos"] += step / dist * speed
                flying.append(m)
        self.missiles = flying
        for b in self.blasts:
            b[2] -= 1
        self.blasts = [b for b in self.blasts if b[2] > 0]

    def _birth(self, p):
        """Parent p has a child: inherits traits (with mutation), usually the
        parent's clan, and appears next to them."""
        i = self.free_slot()
        self._spawn(i)
        self.stats["born"] += 1
        r = self.rng
        self.ethnicity[i] = self.ethnicity[p] if r.random() > 0.02 else r.integers(0, len(ETHNIC_COLORS))
        self.aggression[i] = np.clip(self.aggression[p] + r.normal(0, 0.08), 0, 1)
        self.prejudice[i] = np.clip(self.prejudice[p] + r.normal(0, 0.08), 0, 1)
        self.strength[i] = np.clip(self.strength[p] * math.exp(r.normal(0, 0.05)), 0.5, 3)
        self.speed_mult[i] = np.clip(self.speed_mult[p] * math.exp(r.normal(0, 0.05)), 0.5, 2.5)
        self.mutant[i] = self.mutant[p] and r.random() < 0.5
        if r.random() < 0.04:                                 # rare big mutation
            kind = r.integers(0, 3)
            if kind == 0: self.strength[i] = min(3, self.strength[i] * 1.6)
            elif kind == 1: self.speed_mult[i] = min(2.5, self.speed_mult[i] * 1.5)
            else: self.aggression[i] = r.choice([0.0, 1.0])
            self.mutant[i] = True
            self.stats["mutants"] += 1
        self.parent[i] = p
        if self.clan[p] >= 0:                                 # children always join the family clan
            self.clan[i] = self.clan[p]
            kin = self.clan_members(self.clan[p])
            self.trust[i, kin] = np.maximum(self.trust[i, kin], 2.0)
            self.trust[kin, i] = np.maximum(self.trust[kin, i], 2.0)
        self.trust[i, p] = self.trust[p, i] = 6.0              # parent and child
        q = np.clip(self.pos[p] + r.normal(0, 1.0, 2), 0.5, [self.W - 0.5, self.H - 0.5])
        if not self.is_water(q):
            self.pos[i] = q

    def _die(self, i):
        self.alive[i] = False
        self.food_inv[i] = self.wood_inv[i] = 0
        self.state[i] = "dead"
        self.clan[i] = -1
        self.admin_guns[i] = False

    # -- time --------------------------------------------------------------------
    def tick(self):
        self.t += 1
        a = self.alive
        self.hunger[a] += 0.06
        self.thirst[a] += 0.09
        starving = a & ((self.hunger >= 100) | (self.thirst >= 100))
        self.health[starving] -= 0.4
        resting = a & np.array([s == "resting" for s in self.state]) & ~starving
        self.health[resting] = np.minimum(100, self.health[resting] + 0.3)
        for i in np.where(a & (self.health <= 0))[0]:
            self._die(i)
            self.stats["starved"] += 1
        idx = np.where(self.alive)[0]
        if len(idx) > 1:
            diff = self.pos[idx, None] - self.pos[None, idx]
            dist = np.hypot(diff[..., 0], diff[..., 1]) + np.eye(len(idx))
            push = (diff / dist[..., None] * (dist < 0.6)[..., None] * 0.02).sum(1)
            for k, i in enumerate(idx):
                if not self.is_water(self.pos[i] + push[k]):
                    self.pos[i] += push[k]
        self.cooldown = np.maximum(0, self.cooldown - 1)
        self.hit_flash = np.maximum(0, self.hit_flash - 1)
        self.strike = {k: (v, f - 1) for k, (v, f) in self.strike.items() if f > 1}
        self.clan_enemy = {c: (e, f - 1) for c, (e, f) in self.clan_enemy.items()
                           if f > 1 and self.alive[e]}
        self.trust *= 0.9995                                       # grudges and friendships fade
        self.shots = {k: (v, f - 1) for k, (v, f) in self.shots.items() if f > 1}
        if self.t % 30 == 0:
            self._update_clans()
        if self.t % 200 == 0:
            self._plan_raids()
        if self.t % 300 == 150:
            self._rebellions()
        living = set(self.clan[self.alive])
        self.forts = {c: f for c, f in self.forts.items() if c in living}   # abandoned forts fall
        for c, f in self.forts.items():                       # defenders heal inside the walls
            if f["level"] > 0:
                m = self.clan_members(c)
                inside = m[np.hypot(*(self.pos[m] - f["pos"]).T) <= FORT_RADIUS[f["level"]]]
                self.health[inside] = np.minimum(100, self.health[inside] + 0.1 * f["level"])
        self.food = np.minimum(3, self.food + 0.004 * (self.terrain == GRASS))
        self.dead_for[~self.alive] += 1
        # ageing and old age
        self.age[self.alive] += 1
        for i in np.where(self.alive & (self.age > self.lifespan))[0]:
            self._die(i)
            self.stats["old age"] += 1
        # births: healthy, fed adults with spare food have children (no population cap)
        fit = self.alive & (self.age > ADULT_AGE) & (self.hunger < 60) & (self.thirst < 60) \
            & (self.health > 50) & (self.food_inv >= 1)
        for p in np.where(fit & (self.rng.random(self.n) < BIRTH_CHANCE))[0]:
            self.food_inv[p] -= 1
            self._birth(p)
        # immigrants arrive only if the island is nearly empty
        if self.alive.sum() < 4:
            for _ in range(6):
                k = self.free_slot()
                self._spawn(k)
                self.aggression[k] = self.rng.uniform(0, 1)
                self.ethnicity[k] = self.rng.integers(0, len(ETHNIC_COLORS))
        self._weapons_of_mass_destruction()


# ---------------------------------------------------------------------------
class RuleAI:
    def __init__(self, world):
        self.w = world

    def act(self, i):
        w, r = self.w, self.w.rng
        p = w.pos[i]
        aggr = w.aggression[i]
        hungry, thirsty = w.hunger[i] >= 45, w.thirst[i] >= 50
        near, _ = w.nearest_person(i, w.reach(i), enemies_only=True)

        def go(target, state):
            w.state[i] = state
            w.move(i, np.asarray(target) - p)

        kid = w.is_child(i)

        # 0. clan war: help clanmates against their enemy
        c = w.clan[i]
        if c >= 0 and c in w.clan_enemy and w.health[i] > 30 and not kid:
            enemy = w.clan_enemy[c][0]
            if not w.friendly(i, enemy):
                d = np.hypot(*(w.pos[enemy] - p))
                if d <= w.reach(i):
                    w.state[i] = "defending clan"
                    return w.attack(i, enemy)
                if d < 9:
                    return go(w.pos[enemy], "defending clan")
        # 0b. raid: the clan marches together on another clan
        urgent = w.hunger[i] > 75 or w.thirst[i] > 80 or w.health[i] < 30
        if c >= 0 and c in w.raids and not urgent and not kid:
            target = w.raids[c][0]
            foes = w.clan_members(target)
            foes = foes[[not w.is_child(k) for k in foes]] if len(foes) else foes
            if len(foes):
                d = np.hypot(*(w.pos[foes] - p).T)
                k = np.argmin(d)
                if d[k] <= w.reach(i):
                    w.state[i] = "raiding"
                    return w.attack(i, foes[k])
                lead = w.leader(c)
                if lead == i or d[k] < 4:                         # leader (or close): charge
                    return go(w.pos[foes[k]], "raiding")
                if np.hypot(*(w.pos[lead] - p)) > 1.5:            # others: keep formation
                    return go(w.pos[lead], "raiding")
                return go(w.pos[foes[k]], "raiding")
        # 1. fight whoever is in reach; trust makes it much less likely
        if near is not None and not kid:
            trust = max(0.0, w.trust[i, near])
            chance = aggr * 0.35 / (1 + trust) * (1 + 2 * w.bias(i, near))   # prejudice fuels violence
            greedy = hungry and w.food_inv[near] >= 1 and w.food_inv[i] < 1 and trust < 2
            grudge = w.trust[i, near] < -3
            if r.random() < chance or greedy or grudge or (w.state[i] == "fighting" and r.random() < aggr):
                w.state[i] = "fighting"
                return w.attack(i, near)
        # 2. drink
        if thirsty:
            if w.near_water(i):
                w.state[i] = "drinking"
                return w.drink(i)
            if len(w.water_pts):
                k = np.argmin(np.hypot(*(w.water_pts - p).T))
                return go(w.water_pts[k], "going to water")
        # 3. eat
        if hungry:
            x, y = w.tile(p)
            if w.food_inv[i] >= 1 or w.food[y, x] >= 1:
                w.state[i] = "eating"
                return w.eat(i)
            spots = np.argwhere(w.food >= 1)[:, ::-1] + 0.5
            if len(spots):
                k = np.argmin(np.hypot(*(spots - p).T))
                return go(spots[k], "looking for food")
        # 4. heal
        if w.health[i] < 50 and near is None:
            w.state[i] = "resting"
            return
        # 5. advance tools
        if w.can_craft(i):
            w.state[i] = "crafting"
            return w.craft(i)
        if w.can_upgrade_weapon(i):
            w.state[i] = "crafting"
            return w.upgrade_weapon(i)
        # 5b. build the clan fortress
        if c >= 0 and w.wood_inv[i] >= 3 and len(w.clan_members(c)) >= FORT_CLAN \
                and w.forts.get(c, {"level": 0})["level"] < 3:
            site = w.forts[c]["pos"] if c in w.forts else w.clan_centre(c)
            if np.hypot(*(site - p)) < 1.2:
                w.state[i] = "building fort"
                return w.build(i)
            return go(site, "hauling wood")
        # 6. hunt (aggressive people go after non-clanmates they don't trust)
        if aggr > 0.5 and not kid:
            others = [j for j in np.where(w.alive)[0]
                      if j != i and not w.friendly(i, j) and w.trust[i, j] < 2 and not w.is_child(j)]
            if others:
                others = np.array(others)
                d = np.hypot(*(w.pos[others] - p).T)
                k = np.argmin(d)
                if d[k] < 7:
                    return go(w.pos[others[k]], "hunting")
        # 7. be kind to whoever is close (builds trust -> clans)
        anyone, _ = w.nearest_person(i)
        if anyone is not None:
            if w.food_inv[i] >= 2 and (w.food_inv[anyone] < 1 or w.friendly(i, anyone)) \
                    and r.random() < (1.2 - aggr) * (1 - 0.8 * w.bias(i, anyone)):
                w.state[i] = "sharing"
                return w.share(i)
            w.state[i] = "trading"
            return w.trade(i)
        # 8. work toward the next tool, else gather food, else wander
        x, y = w.tile(p)
        need_wood = w.tool[i] < len(TOOL_COST) and w.wood_inv[i] < TOOL_COST[w.tool[i]][0]
        builder = c >= 0 and len(w.clan_members(c)) >= FORT_CLAN and \
            w.forts.get(c, {"level": 0})["level"] < 3
        if w.terrain[y, x] == FOREST and (need_wood or w.wood_inv[i] < (4 if builder else 2)):
            w.state[i] = "gathering wood"
            return w.gather(i)
        if w.food[y, x] >= 1 and w.food_inv[i] < 6:
            w.state[i] = "gathering food"
            return w.gather(i)
        if (need_wood or (builder and w.wood_inv[i] < 3)) and r.random() < 0.6:
            forest = np.argwhere(w.terrain == FOREST)[:, ::-1] + 0.5
            if len(forest):
                k = np.argmin(np.hypot(*(forest - p).T))
                return go(forest[k], "going to forest")
        if w.wander[i] is None or np.hypot(*(w.wander[i] - p)) < 0.5 or r.random() < 0.005:
            centre = w.clan_centre(c) if c >= 0 else None
            if centre is not None:                                 # stay with the clan
                w.wander[i] = np.clip(centre + r.normal(0, 2, 2), 1, [w.W - 1, w.H - 1])
            else:
                w.wander[i] = r.uniform(1, [w.W - 1, w.H - 1])
        go(w.wander[i], "wandering")


# ---------------------------------------------------------------------------
TERRAIN_RGB = np.array([[70, 160, 70], [210, 130, 40], [35, 95, 40]], np.uint8)   # BGR
PANEL_BG = (60, 35, 20)          # deep blue (BGR)


def render(w, cell, panel_h, sel, control_all, paused, speed, target=None, admin=None,
           multi=(), box=None):
    base = TERRAIN_RGB[w.terrain].astype(np.float32)
    shade = np.where(w.terrain == GRASS, 0.55 + w.food / 6.5, 1.0)[..., None]
    img = np.repeat(np.repeat((base * shade).astype(np.uint8), cell, 0), cell, 1)
    for i, (j, _) in w.strike.items():
        a = tuple(int(v) for v in w.pos[i] * cell)
        b = tuple(int(v) for v in w.pos[j] * cell)
        cv2.line(img, a, b, (0, 0, 255), max(3, cell // 7))
    rad = max(4, cell // 2 - 3)
    for a, (b, _) in w.raids.items():                               # raid arrows between clans
        ca, cb = w.clan_centre(a), w.clan_centre(b)
        if ca is not None and cb is not None:
            cv2.arrowedLine(img, tuple(int(v) for v in ca * cell), tuple(int(v) for v in cb * cell),
                            CLAN_COLORS[a % len(CLAN_COLORS)], 3, tipLength=0.04)
    for (i, _v), (j, _) in w.shots.items():                        # gunshots: yellow tracer
        cv2.line(img, tuple(int(v) for v in w.pos[i] * cell), tuple(int(v) for v in w.pos[j] * cell),
                 (0, 255, 255), 1 if w.weapon[i] == 2 else 2)
    P = lambda v: tuple(int(x) for x in np.asarray(v) * cell)
    for m in w.missiles:                                            # missiles & nukes in flight
        nuke = m["kind"] == "nuke"
        cv2.line(img, P(m["start"]), P(m["pos"]), (200, 200, 200) if not nuke else (0, 255, 255), 1)
        cv2.circle(img, P(m["pos"]), 9 if nuke else 5, (0, 0, 0) if nuke else (0, 0, 255), -1)
        cv2.circle(img, P(m["target"]), int((6 if nuke else 2) * cell), (0, 0, 255), 1)
    for pos, radius, frames, kind in w.blasts:                      # explosions
        total = 45 if kind == "nuke" else 14
        grow = 1 - frames / total
        over = img.copy()
        cv2.circle(over, P(pos), int(radius * cell * (0.3 + 0.7 * grow)),
                   (255, 255, 255) if kind == "nuke" and grow < 0.2 else (0, 120, 255), -1)
        cv2.circle(over, P(pos), int(radius * cell * (0.15 + 0.35 * grow)), (0, 230, 255), -1)
        cv2.addWeighted(over, 0.75 * frames / total + 0.15, img, 1 - (0.75 * frames / total + 0.15), 0, img)
    for c, f in w.forts.items():                                   # fortresses
        if f["level"] == 0:
            cv2.putText(img, f"fort site {f['wood']:.0f}/{FORT_COST[0]}", P(f["pos"]),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (40, 40, 40), 1, cv2.LINE_AA)
            continue
        r_ = int(FORT_RADIUS[f["level"]] * cell)
        cx, cy = P(f["pos"])
        col = CLAN_COLORS[c % len(CLAN_COLORS)]
        over = img.copy()
        cv2.rectangle(over, (cx - r_, cy - r_), (cx + r_, cy + r_), (110, 110, 120), -1)
        cv2.addWeighted(over, 0.25, img, 0.75, 0, img)
        cv2.rectangle(img, (cx - r_, cy - r_), (cx + r_, cy + r_), (90, 90, 100), 3 + 2 * f["level"])
        cv2.rectangle(img, (cx - r_, cy - r_), (cx + r_, cy + r_), col, 1)
        for tx, ty in ((cx - r_, cy - r_), (cx + r_, cy - r_), (cx - r_, cy + r_), (cx + r_, cy + r_)):
            cv2.rectangle(img, (tx - 7, ty - 7), (tx + 7, ty + 7), (70, 70, 80), -1)   # towers
            cv2.rectangle(img, (tx - 7, ty - 7), (tx + 7, ty + 7), col, 2)
        cv2.putText(img, f"FORT #{c} L{f['level']}", (cx - r_, cy - r_ - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (30, 30, 30), 2, cv2.LINE_AA)
    leaders = {w.leader(c) for c in set(w.clan[w.alive]) if c >= 0}
    for i in np.where(w.alive)[0]:
        c = tuple(int(v) for v in w.pos[i] * cell)
        col = (255, 255, 255) if w.hit_flash[i] else STATE_COLOR.get(w.state[i], (225, 225, 225))
        ring = CLAN_COLORS[w.clan[i] % len(CLAN_COLORS)] if w.clan[i] >= 0 else (90, 90, 90)
        rr = rad if not w.is_child(i) else max(4, int(rad * 0.65))   # children are smaller
        cv2.circle(img, c, rr, col, -1)
        cv2.circle(img, c, rr, ring, max(2, cell // 9))
        if w.invincible[i]:
            cv2.circle(img, c, rad + 9, (255, 255, 255), 1)
        cv2.circle(img, c, max(3, rad // 3), ETHNIC_COLORS[w.ethnicity[i]], -1)   # ethnicity
        if w.mutant[i]:                                             # mutants glow green
            cv2.circle(img, c, rad + 3, (60, 255, 60), 1)
        tip = (int(c[0] + math.cos(w.heading[i]) * rad), int(c[1] + math.sin(w.heading[i]) * rad))
        cv2.line(img, c, tip, (0, 0, 0), 2)
        for k in range(w.tool[i]):                                  # tool level pips
            cv2.circle(img, (c[0] - rad + 4 + k * 7, c[1] + rad + 5), 3, (0, 200, 255), -1)
        hp = int(2 * rad * max(0, w.health[i]) / 100)
        cv2.line(img, (c[0] - rad, c[1] - rad - 5), (c[0] - rad + hp, c[1] - rad - 5), (0, 0, 255), 3)
        if i in leaders:                                            # crown for clan leaders
            pts = np.array([[c[0] - rad // 2, c[1] - rad - 8], [c[0], c[1] - rad - 16],
                            [c[0] + rad // 2, c[1] - rad - 8]])
            cv2.fillPoly(img, [pts], (0, 0, 255) if w.clan[i] == w.warlord else (0, 215, 255))
        if w.has_gun(i):                       # gun barrel: pistol / shotgun / MG / sniper
            length, thick, gcol = [(9, 2, (20, 20, 20)), (8, 4, (30, 60, 110)),
                                   (13, 3, (70, 70, 70)), (18, 1, (10, 10, 10))][w.weapon[i]]
            cv2.rectangle(img, (c[0] + rad - 2, c[1] - thick), (c[0] + rad + length, c[1] + thick), gcol, -1)
        if i == target:
            cv2.circle(img, c, rad + 8, (0, 140, 255), 2)
        if i in multi:                                             # group selection
            cv2.circle(img, c, rad + 6, (255, 255, 0), 2)
        if i == sel or control_all:
            cv2.circle(img, c, rad + 5, (255, 0, 255), 2)
    s = w.stats
    clans = {}
    for c in w.clan[w.alive]:
        if c >= 0:
            clans[c] = clans.get(c, 0) + 1
    top = sorted(clans.items(), key=lambda kv: -kv[1])[:6]
    tech = np.bincount(w.tool[w.alive], minlength=4)
    if w.warlord is not None:
        cc = w.clan_centre(w.warlord)
        if cc is not None:
            cv2.putText(img, f"{TITLE} #{w.warlord} ({w.clan_kills[w.warlord]} kills)",
                        (int(cc[0] * cell) - 80, int(cc[1] * cell) - cell * 2),
                        cv2.FONT_HERSHEY_DUPLEX, 0.8, (0, 0, 255), 2, cv2.LINE_AA)
    lines = [
        (f"t={w.t}  population {w.alive.sum()}  born {s['born']}  old age {s['old age']}  attacks {s['attacks']}  kills {s['kills']}  "
         f"starved {s['starved']}  shares {s['shares']}  trades {s['trades']}  clan joins {s['joined']}  "
         f"tools crafted {s['crafted']}  speed x{speed}" + ("  [PAUSED]" if paused else ""), (255, 255, 255)),
        (f"clans: {len(clans)}  " + "  ".join(f"#{c}:{n}" for c, n in top)
         + "   raids: " + (", ".join(f"#{a}->#{b}" for a, (b, _) in w.raids.items()) or "none")
         + f"   guns: {sorted(int(c) for c in w.gun_clans) or '-'}"
         + (f"   {TITLE}: #{w.warlord}" if w.warlord is not None else "")
         + f"    tech: " + "  ".join(f"{TOOLS[k]} {tech[k]}" for k in range(4))
         + "   arms: " + " ".join(f"{WEAPONS[k][0]} {(w.has_gun_mask() & (w.weapon == k)).sum()}" for k in range(4))
         + f"   missiles {s['missiles']} nukes {s['nukes']} mutants {s['mutants']}"
         + f"   discrimination {w.discrimination:.1f}"
         + f"   forts: " + (", ".join(f"#{c} L{f['level']}" for c, f in w.forts.items()) or "-")
         + f"   rebellions {s['rebellions']}   children {int((w.alive & (w.age < ADULT_AGE)).sum())}",
         (120, 230, 255)),
        (f"selected #{sel}: {w.state[sel]}  clan {w.clan[sel] if w.clan[sel] >= 0 else '-'}  "
         f"tool {TOOLS[w.tool[sel]]}  hunger {w.hunger[sel]:.0f}  thirst {w.thirst[sel]:.0f}  "
         f"health {w.health[sel]:.0f}  food {w.food_inv[sel]:.0f}  wood {w.wood_inv[sel]:.0f}  "
         f"age {w.age[sel] / 1000:.1f}k  aggression {w.aggression[sel]:.2f}  ethnicity {ETHNIC_NAMES[w.ethnicity[sel]]}  "
         f"prejudice {w.prejudice[sel]:.2f}  strength {w.strength[sel]:.2f}  speed {w.speed_mult[sel]:.2f}  "
         f"weapon {WEAPONS[w.weapon[sel]][0] if w.has_gun(sel) else '-'}"
         + ("  MUTANT" if w.mutant[sel] else ""), (140, 255, 140)),
        (f"control: {'ALL' if control_all else 'selected'} [0]  click/Tab select  WASD walk  R rest  "
         "E eat  F drink  G gather  C craft  H share  T trade  X attack  space pause  +/- speed  "
         "M admin  Esc deselect  Q quit  |  ring=clan  centre dot=ethnicity  green halo=mutant", (200, 200, 255)),
    ]
    panel = np.zeros((panel_h, img.shape[1], 3), np.uint8)
    panel[:] = PANEL_BG
    gap = panel_h // len(lines)
    fs = min(0.6, gap / 40)
    for k, (txt, col) in enumerate(lines):
        cv2.putText(panel, txt, (10, gap * k + int(gap * 0.7)),
                    cv2.FONT_HERSHEY_SIMPLEX, fs, col, 1, cv2.LINE_AA)
    if box is not None and len(box) == 2:
        cv2.rectangle(img, box[0], box[1], (255, 255, 0), 2)
    if admin is not None:
        draw_admin(img, admin, w, sel, target, multi)
    return np.vstack([img, panel])


ADMIN_HELP = [
    "ADMIN PANEL  (M closes)   click = selected (pink)   right-click = target (orange)",
    "Shift+click add/remove one   Shift+drag box-select a group (cyan)   C clear group",
    "1  new clan from the group (or selected + everyone within 3 tiles)",
    "2  group/selected joins target's clan",
    "3  kick group/selected from their clan",
    "4  selected's clan RAIDS target's clan",
    "5  ALL clans attack target's clan",
    "6  peace: cancel all raids and grudges",
    "7  spawn 10 near selected   Y  spawn 10 at mouse   O  50 immigrants across the island",
    "8  kill group/selected   Z  wipe out target's whole clan",
    "9  heal + feed group/selected   I  invincible on/off (group)   L  teleport group to mouse",
    "G  give/take guns (group)          U  upgrade tools (group)",
    "[  ]  aggression of selected -/+    F  feast: fill all grass with food",
    "K  give selected's clan the title (+10 kills)      W  WORLD WAR (4 armed armies)",
    "V  cycle gun type of group (pistol/shotgun/MG/sniper)",
    "B  missile from selected's clan at target    N  NUKE at target",
    "P  discrimination level 0 / 0.5 / 1           X  mutate group (random big mutation)",
    "R  rebellion inside selected's clan   H  build max fortress for selected's clan",
    "D  drought (no food)   E  dissolve all clans   J  grow group up to adults",
]


def draw_admin(img, admin, w, sel, target, multi=()):
    x0, y0 = 20, 20
    width, height = 900, 26 * (len(ADMIN_HELP) + 2) + 10
    over = img[y0:y0 + height, x0:x0 + width]
    over[:] = (over * 0.25 + np.array([70, 30, 10]) * 0.75).astype(np.uint8)
    cv2.rectangle(img, (x0, y0), (x0 + width, y0 + height), (0, 200, 255), 2)
    for k, txt in enumerate(ADMIN_HELP):
        cv2.putText(img, txt, (x0 + 12, y0 + 28 + 26 * k), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (0, 220, 255) if k == 0 else (255, 255, 255), 1, cv2.LINE_AA)
    t = "-" if target is None else f"#{target} clan {w.clan[target]}"
    info = f"selected #{sel}  group {len(multi)}  target {t}   last: {admin}"
    cv2.putText(img, info, (x0 + 12, y0 + 28 + 26 * len(ADMIN_HELP) + 6), cv2.FONT_HERSHEY_SIMPLEX,
                0.55, (140, 255, 140), 1, cv2.LINE_AA)


def admin_command(w, key, sel, target, multi=(), cursor=(0, 0)):
    ch = chr(key) if key < 128 else ""
    need_t = "right-click someone to set a target first"
    group = sorted(multi) if multi else [sel]
    if ch == "1":
        if multi:                                   # clan from the shift-selected group
            c = w.next_clan
            w.next_clan += 1
            w.clan[group] = c
            w._update_clans()
            return f"made clan #{c} with {len(group)} members"
        c, n = w.admin_new_clan(sel)
        return f"made clan #{c} with {n} members"
    if ch == "2":
        if target is None: return need_t
        for i in group:
            w.admin_join(i, target)
        w._update_clans()
        return f"{len(group)} joined clan #{w.clan[target]}"
    if ch == "3":
        w.clan[group] = -1
        return f"{len(group)} kicked from their clan"
    if ch == "c":
        multi.clear()
        return "group selection cleared"
    if ch in "45":
        if target is None: return need_t
        if w.clan[target] < 0: w.admin_join(target, target)
        tc = w.clan[target]
        if ch == "4":
            if w.clan[sel] < 0: w.admin_new_clan(sel)
            w.raids[w.clan[sel]] = (tc, 1500)
            return f"clan #{w.clan[sel]} raids clan #{tc}"
        for c in set(w.clan[w.alive]):
            if c >= 0 and c != tc:
                w.raids[c] = (tc, 1500)
        return f"all clans attack clan #{tc}"
    if ch == "6":
        w.raids.clear(); w.clan_enemy.clear()
        w.trust = np.maximum(w.trust, 0)
        return "peace declared"
    if ch == "7":
        return f"spawned {w.admin_spawn(10, w.pos[sel])} humans"
    if ch == "8":
        for i in group:
            w._die(i)
        return f"killed {len(group)}"
    if ch == "9":
        w.health[group], w.hunger[group], w.thirst[group] = 100, 0, 0
        return f"healed {len(group)}"
    if ch == "y":
        return f"spawned {w.admin_spawn(10, np.array(cursor))} at mouse"
    if ch == "o":
        n = 0
        for _ in range(50):
            k = w.free_slot()
            w._spawn(k)
            w.aggression[k] = w.rng.uniform(0, 1)
            w.ethnicity[k] = w.rng.integers(0, len(ETHNIC_COLORS))
            w.age[k] = ADULT_AGE
            n += 1
        return f"{n} immigrants arrived"
    if ch == "z":
        if target is None or w.clan[target] < 0: return "right-click a clan member first"
        tc = w.clan[target]
        m = w.clan_members(tc)
        for k in m:
            w._die(k)
        return f"clan #{tc} wiped out ({len(m)})"
    if ch == "i":
        on = not w.invincible[group[0]]
        w.invincible[group] = on
        return f"{len(group)} invincible {'ON' if on else 'OFF'}"
    if ch == "l":
        for i in group:
            q = np.clip(np.array(cursor) + w.rng.normal(0, 0.8, 2), 0.5, [w.W - 0.5, w.H - 0.5])
            if not w.is_water(q): w.pos[i] = q
        return f"teleported {len(group)}"
    if ch == "r":
        c = w.clan[sel]
        if c < 0: return "selected has no clan"
        m = w.clan_members(c)
        faction = m[np.argsort(np.hypot(*(w.pos[m] - w.pos[sel]).T))[:w.rng.integers(2, 5)]]
        new = w.next_clan; w.next_clan += 1
        w.clan[faction] = new
        w.raids[new] = (c, 1500)
        w.stats["rebellions"] += 1
        return f"{len(faction)} rebels formed clan #{new} against #{c}"
    if ch == "h":
        c = w.clan[sel]
        if c < 0: return "selected has no clan"
        w.forts[c] = {"pos": w.pos[sel].copy(), "level": 3, "wood": 0.0}
        return f"clan #{c} has a level 3 fortress"
    if ch == "d":
        w.food[:] = 0
        return "drought: all food gone"
    if ch == "e":
        w.clan[:] = -1; w.raids.clear(); w.forts.clear()
        return "all clans dissolved"
    if ch == "j":
        w.age[group] = np.maximum(w.age[group], ADULT_AGE)
        return f"{len(group)} grown up"
    if ch == "g":
        give = not w.admin_guns[group[0]]
        w.admin_guns[group] = give
        return f"{len(group)} {'given' if give else 'lost'} guns"
    if ch == "u":
        w.tool[group] = np.minimum(w.tool[group] + 1, len(TOOLS) - 1)
        return f"{len(group)} upgraded tools"
    if ch in "[]":
        w.aggression[sel] = np.clip(w.aggression[sel] + (0.1 if ch == "]" else -0.1), 0, 1)
        return f"#{sel} aggression {w.aggression[sel]:.1f}"
    if ch == "w":
        return f"WORLD WAR: {w.world_war()} armed armies"
    if ch == "v":
        kind = (w.weapon[group[0]] + 1) % len(WEAPONS)
        w.admin_guns[group] = True
        w.weapon[group] = kind
        return f"{len(group)} armed with {WEAPONS[kind][0]}"
    if ch in "bn":
        if target is None: return need_t
        kind = "nuke" if ch == "n" else "missile"
        w.launch(w.clan[sel] if ch == "b" else -1, w.pos[target], kind)
        return f"{kind.upper()} launched at #{target}"
    if ch == "p":
        w.discrimination = {0.0: 0.5, 0.5: 1.0}.get(round(w.discrimination, 1), 0.0)
        return f"discrimination {w.discrimination:.1f}"
    if ch == "x":
        w.strength[group] = np.minimum(3, w.strength[group] * 1.6)
        w.speed_mult[group] = np.minimum(2.5, w.speed_mult[group] * 1.3)
        w.mutant[group] = True
        return f"{len(group)} mutated (stronger + faster)"
    if ch == "f":
        w.food[w.terrain == GRASS] = 3
        return "feast!"
    if ch == "k":
        if w.clan[sel] < 0: return "selected has no clan"
        w.clan_kills[w.clan[sel]] = max(w.clan_kills.values(), default=0) + 10
        w._update_clans()
        return f"clan #{w.clan[sel]} are now the {TITLE}"
    return "unknown key"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--humans", type=int, default=50)
    ap.add_argument("--aggression", type=float, default=0.5, help="average aggression 0..1")
    ap.add_argument("--rows", type=int, default=24, help="island height in tiles (width fits the screen)")
    ap.add_argument("--speed", type=int, default=1)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--war", type=int, default=0, help="start a world war at this frame")
    ap.add_argument("--discrimination", type=float, default=0.5, help="0 = none, 1 = strong")
    args = ap.parse_args()

    sw, sh = screen_size()
    panel_h = max(110, sh // 9)
    cell = max(8, (sh - panel_h) // args.rows)
    cols = sw // cell
    panel_h = sh - cell * args.rows                       # fill the screen exactly
    w = World(args.humans, cols, args.rows, args.aggression, args.seed, args.discrimination)
    ai = RuleAI(w)
    ui = {"sel": 0, "all": False, "paused": False, "click": None, "force": None, "force_t": 0,
          "admin": False, "msg": "-", "target": None, "rclick": None,
          "multi": set(), "drag": None, "box": None, "shift_click": None, "cursor": (1.0, 1.0)}
    speed = args.speed
    win = "Life"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.setWindowProperty(win, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    def on_mouse(e, x, y, f, p):
        shift = bool(f & cv2.EVENT_FLAG_SHIFTKEY)
        ui["cursor"] = (x / cell, y / cell)
        if e == cv2.EVENT_LBUTTONDOWN and shift:
            ui["drag"] = (x, y)
        elif e == cv2.EVENT_MOUSEMOVE and ui["drag"] is not None:
            ui["box"] = (ui["drag"], (x, y))
        elif e == cv2.EVENT_LBUTTONUP and ui["drag"] is not None:
            (x0, y0), ui["drag"] = ui["drag"], None
            if abs(x - x0) + abs(y - y0) < 6:
                ui["shift_click"] = (x / cell, y / cell)      # shift+click: toggle one
            else:
                ui["box"] = ((x0, y0), (x, y), "done")         # shift+drag: box select
        elif e == cv2.EVENT_LBUTTONDOWN:
            ui["click"] = (x / cell, y / cell)
        elif e == cv2.EVENT_RBUTTONDOWN:
            ui["rclick"] = (x / cell, y / cell)
    cv2.setMouseCallback(win, on_mouse)
    walk = {ord("w"): (0, -1), ord("s"): (0, 1), ord("a"): (-1, 0), ord("d"): (1, 0)}
    acts = {ord("r"): "rest", ord("e"): "eat", ord("f"): "drink", ord("g"): "gather",
            ord("h"): "share", ord("t"): "trade", ord("x"): "attack", ord("c"): "craft"}

    def player(i, cmd):
        if cmd in walk.values():
            w.state[i] = "player: walking"
            w.move(i, cmd)
        elif cmd == "rest":
            w.state[i] = "resting"
        else:
            w.state[i] = {"attack": "fighting", "share": "sharing", "trade": "trading",
                          "drink": "drinking", "eat": "eating", "craft": "crafting"}.get(cmd, "gathering")
            getattr(w, cmd)(i)

    while True:
        alive = np.where(w.alive)[0]
        if ui["click"] is not None and len(alive):
            d = np.hypot(*(w.pos[alive] - np.array(ui["click"])).T)
            ui["sel"] = int(alive[np.argmin(d)])
            ui["click"] = None
        if ui["shift_click"] is not None and len(alive):
            d = np.hypot(*(w.pos[alive] - np.array(ui["shift_click"])).T)
            k = int(alive[np.argmin(d)])
            ui["multi"] ^= {k}
            ui["shift_click"] = None
        if ui["box"] is not None and len(ui["box"]) == 3:
            (x0, y0), (x1, y1), _ = ui["box"]
            lo = np.array([min(x0, x1), min(y0, y1)]) / cell
            hi = np.array([max(x0, x1), max(y0, y1)]) / cell
            inside = alive[((w.pos[alive] >= lo) & (w.pos[alive] <= hi)).all(1)]
            ui["multi"] |= set(int(i) for i in inside)
            ui["box"] = None
        ui["multi"] = {i for i in ui["multi"] if w.alive[i]}
        if ui["rclick"] is not None and len(alive):
            d = np.hypot(*(w.pos[alive] - np.array(ui["rclick"])).T)
            ui["target"] = int(alive[np.argmin(d)])
            ui["rclick"] = None
        if not w.alive[ui["sel"]] and len(alive):
            ui["sel"] = int(alive[0])
        if ui["target"] is not None and not w.alive[ui["target"]]:
            ui["target"] = None
        cv2.imshow(win, render(w, cell, panel_h, ui["sel"], ui["all"], ui["paused"], speed,
                               ui["target"], ui["msg"] if ui["admin"] else None,
                               ui["multi"], ui["box"]))
        key = cv2.waitKey(16) & 0xFF
        if key == ord("q") or cv2.getWindowProperty(win, cv2.WND_PROP_VISIBLE) < 1:
            break
        if key == 27:                                   # Esc: deselect everything
            ui["multi"].clear()
            ui["target"] = None
            ui["all"] = False
            ui["msg"] = "deselected"
            key = 255
        if key == ord("m"):
            ui["admin"] = not ui["admin"]
            key = 255
        if ui["admin"] and key != 255:
            ui["msg"] = admin_command(w, key, ui["sel"], ui["target"], ui["multi"], ui["cursor"])
            key = 255                                   # admin keys don't control humans
        if key == ord("0"):
            ui["all"] = not ui["all"]
        elif key == 9 and len(alive):
            later = alive[alive > ui["sel"]]
            ui["sel"] = int(later[0] if len(later) else alive[0])
        elif key == ord(" "):
            ui["paused"] = not ui["paused"]
        elif key in (ord("+"), ord("=")):
            speed = min(speed * 2, 64)
        elif key == ord("-"):
            speed = max(speed // 2, 1)
        if key in walk or key in acts:
            ui["force"] = walk.get(key) or acts[key]
            ui["force_t"] = 10 if key in walk else 1
        if ui["paused"] and key != ord("n") and ui["force"] is None:
            continue
        for _ in range(1 if ui["paused"] else speed):
            controlled = set(np.where(w.alive)[0]) if ui["all"] else {ui["sel"]}
            for i in np.where(w.alive)[0]:
                if not w.alive[i]:
                    continue
                if ui["force"] is not None and i in controlled:
                    player(i, ui["force"])
                else:
                    ai.act(i)
            if ui["force"] is not None:
                ui["force_t"] -= 1
                if ui["force_t"] <= 0:
                    ui["force"] = None
            w.tick()
            if args.war and w.t == args.war:
                ui["msg"] = f"WORLD WAR: {w.world_war()} armed armies"
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
