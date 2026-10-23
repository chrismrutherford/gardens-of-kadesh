"""Spec 004 twin-core: configuration (driver-runtime.md §2, T004).

JSON config → validated typed config. Defaults derived from research D9 with
game-time conversions at TICKS_PER_SEC = 16 ticks per second of game time
(engine aiplay.c:566: 128 ticks ≈ 8s). Difficulty presets re-use the ONE
directive/budget schema — parameters only (FR-026); explicit user values win
over preset-derived values. Endpoint URLs are configuration, local/LAN only
(FR-028, constitution security).
"""
import copy
import json
import os

TICKS_PER_SEC = 16.0

DEFAULT_DIFFICULTY = "intermediate"

_PRESET_KEYS = None  # set after DEFAULTS; validated equal across presets


def seconds_to_ticks(seconds):
    return max(1, int(round(seconds * TICKS_PER_SEC)))


DEFAULTS = {
    "seat": {"player_index": 0},
    "transport": {"mcp_url": "http://127.0.0.1:8302/mcp"},
    "channels": {
        "s1": {"url": "", "model": "", "max_context": 8192,
               "max_output": 200, "temperature": 0.2},
        "s2": {"url": "", "model": "", "max_context": 24576,
               "max_output": 2048, "temperature": 0.4},
    },
    "cadence": {
        "tick_ticks": seconds_to_ticks(1.0),   # FR-029 ~1s game time
        "combat_gate_multiplier": 3,
        "pulse_ticks": seconds_to_ticks(56.25),  # 30-90s band midpoint-ish
        "p0_wake": True,
    },
    "digest": {
        "window_pulses": 8,
        "rollup_interval_ticks": 10 * seconds_to_ticks(1.0),
        "p0_rules": [
            "command_centre_attacked",
            "command_centre_lost",
            "defended_zone_breach",
            "battle_group_annihilated",
            "ru_flow_zeroed_while_bank_high",
        ],
    },
    "sticky": {
        "evidence_min": 2,                     # ≥2 corroborations (D9)
        "hold_ticks": seconds_to_ticks(60.0),  # ≥60s game time (D9)
    },
    "budgets": {
        "s1_in": 8192, "s1_out": 200,
        "digest_min": 300, "digest_max": 800,
        "s2_in": 24576, "s2_out": 2048,
    },
    "features": {
        "mines": True, "cloak": True, "gravwell": True,
        "kamikaze_draw": 0.25, "armada_teams": 2,
    },
    "economy": {
        "build_split": {"attack": 30, "defence": 30, "economy": 40},
        "income_target_percent": 100,
    },
    "recorder": {"dsn": {"host": "192.168.1.152", "dbname": "twincore_q",
                         "user": "postgres",
                         "connect_timeout": 5}},  # postgres record store
                                                    # (FR-021/FR-030; no files)
    "v4": {  # spec 005 three-tier driver (decision 17)
        "pulse_seconds": 120,       # S2: clamp 90-240 (FR-007, ~2min latency)
        "micro_pulse_seconds": 8,   # S1: clamp 5-15
        "tick_seconds": 1,          # executor tick
        "max_commands_per_tick": 6,  # FR-005, all channels combined
        "max_repair_attempts": 1,   # ≤1 bounded repair per pulse
        "reflexes": {"harvest": True, "deliver": True},  # FR-006 exact set
        "s1_channel": {"enabled": True},  # off ⇒ program + reflexes only
        "budgets": {"s2_in": 12288, "s2_out": 3072, "s1_in": 4096,
                    "s1_out": 900},
        # Draft v3 (decision 19): teaching firewall + arc + superset view
        "arc_chars": 600,          # FR-021: S2 arc budget (length-clamped)
        "verdict_chars": 200,      # FR-020: one verdict line budget
        "asteroid_top_n": 32,      # FR-009: asteroid ledger rows (nearest)
        "verdict_feed_k": 6,       # last K verdicts rendered to S1
    },
    "strategist": {"enabled": False},
    "difficulty": DEFAULT_DIFFICULTY,
}

# Difficulty presets: playbook B3-derived parameter sets (FR-026). Cadence
# per the game's own difficulty data (D4: ≈2.1s / 1s / 0.5s think time).
DIFFICULTY_PRESETS = {
    "beginner": {
        "cadence": {"tick_ticks": seconds_to_ticks(2.1)},
        "features": {"mines": False, "cloak": False, "gravwell": False,
                     "kamikaze_draw": 0.0, "armada_teams": 0},
        "economy": {"research_delay_ticks": seconds_to_ticks(90.0),
                    "reaction_threshold_pct": 110},
    },
    "intermediate": {
        "cadence": {"tick_ticks": seconds_to_ticks(1.0)},
        "features": {"mines": False, "cloak": False, "gravwell": False,
                     "kamikaze_draw": 0.0, "armada_teams": 1},
        "economy": {"research_delay_ticks": seconds_to_ticks(45.0),
                    "reaction_threshold_pct": 105},
    },
    "advanced": {
        "cadence": {"tick_ticks": seconds_to_ticks(0.5)},
        "features": {"mines": True, "cloak": True, "gravwell": True,
                     "kamikaze_draw": 0.25, "armada_teams": 2},
        "economy": {"research_delay_ticks": seconds_to_ticks(15.0),
                    "reaction_threshold_pct": 100},
    },
}

ECONOMY_DEFAULTS = {"research_delay_ticks": seconds_to_ticks(45.0),
                    "reaction_threshold_pct": 105}

_PRESET_KEYS = None


def _check_preset_shape():
    """One schema — presets vary parameters only (FR-026)."""
    global _PRESET_KEYS
    if _PRESET_KEYS is not None:
        return
    keys = None
    for level, preset in DIFFICULTY_PRESETS.items():
        k = set(preset)
        assert len(k) == len(preset)
        if keys is None:
            keys = k
        else:
            assert keys == k, "preset %s diverges in shape" % level
    _PRESET_KEYS = keys


class ConfigError(ValueError):
    pass


class Config:
    def __init__(self, data):
        _check_preset_shape()
        if not isinstance(data, dict):
            raise ConfigError("config root must be a JSON object")
        unknown = set(data) - set(DEFAULTS)
        if unknown:
            raise ConfigError("unknown config keys: %s" % sorted(unknown))
        user = copy.deepcopy(data)

        level = user.get("difficulty", DEFAULT_DIFFICULTY)
        if level not in DIFFICULTY_PRESETS:
            raise ConfigError("unknown difficulty %r" % (level,))

        merged = copy.deepcopy(DEFAULTS)
        preset = copy.deepcopy(DIFFICULTY_PRESETS[level])
        # preset fills economy extras not listed in DEFAULTS.economy
        for k, v in preset.get("economy", {}).items():
            merged["economy"].setdefault(k, ECONOMY_DEFAULTS.get(k, v))
            merged["economy"][k] = v
        preset.pop("economy")
        _deep_update(merged, preset)
        _deep_update(merged, user)
        self._d = merged
        self._validate()

    # -- accessors --------------------------------------------------------
    def get(self, path, default=None):
        node = self._d
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def as_dict(self):
        return copy.deepcopy(self._d)

    def __repr__(self):
        return "Config(%r)" % (self._d["difficulty"],)

    # -- validation --------------------------------------------------------
    def _validate(self):
        seat = self._d["seat"]["player_index"]
        if not isinstance(seat, int) or isinstance(seat, bool) or seat < 0:
            raise ConfigError("seat.player_index must be an int ≥ 0")
        url = self._d["transport"]["mcp_url"]
        if not isinstance(url, str) or not url.startswith("http"):
            raise ConfigError("transport.mcp_url must be an http(s) URL")
        for name, ch in self._d["channels"].items():
            ctx = ch.get("max_context", 0)
            out = ch.get("max_output", 0)
            if not isinstance(ctx, int) or ctx <= 0:
                raise ConfigError("channels.%s.max_context must be > 0" % name)
            if not isinstance(out, int) or out <= 0:
                raise ConfigError("channels.%s.max_output must be > 0" % name)
        for b in self._d["budgets"].values():
            if not isinstance(b, int) or b <= 0:
                raise ConfigError("all budgets must be ints > 0")
        dmin = self._d["budgets"]["digest_min"]
        dmax = self._d["budgets"]["digest_max"]
        if dmax <= dmin:
            raise ConfigError("budgets.digest_max must exceed digest_min")
        for k in ("tick_ticks", "pulse_ticks", "combat_gate_multiplier"):
            v = self._d["cadence"][k]
            if not isinstance(v, int) or v <= 0:
                raise ConfigError("cadence.%s must be int > 0" % k)
        split = self._d["economy"]["build_split"]
        if sum(split.values()) != 100:
            raise ConfigError("economy.build_split must sum to 100")
        if self._d["digest"]["window_pulses"] < 1:
            raise ConfigError("digest.window_pulses must be ≥ 1")
        if self._d["digest"]["rollup_interval_ticks"] < 1:
            raise ConfigError("digest.rollup_interval_ticks must be ≥ 1")
        if self._d["sticky"]["evidence_min"] < 1:
            raise ConfigError("sticky.evidence_min must be ≥ 1")
        kd = self._d["features"]["kamikaze_draw"]
        if not 0.0 <= kd <= 1.0:
            raise ConfigError("features.kamikaze_draw must be in [0,1]")
        self._validate_v4()

    def _validate_v4(self):
        v4 = self._d["v4"]
        for k in ("pulse_seconds", "micro_pulse_seconds", "tick_seconds",
                  "max_commands_per_tick", "max_repair_attempts"):
            v = v4[k]
            if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
                raise ConfigError("v4.%s must be an int > 0" % k)
        if not 90 <= v4["pulse_seconds"] <= 240:
            raise ConfigError("v4.pulse_seconds must be in [90, 240] (FR-007)")
        if not 5 <= v4["micro_pulse_seconds"] <= 15:
            raise ConfigError("v4.micro_pulse_seconds must be in [5, 15]")
        ceilings = {"s2_in": 12288, "s2_out": 3072, "s1_in": 4096,
                    "s1_out": 900}
        budgets = v4["budgets"]
        if set(budgets) != set(ceilings):
            raise ConfigError("v4.budgets keys must be %s" % sorted(ceilings))
        for k, ceil in ceilings.items():
            v = budgets[k]
            if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
                raise ConfigError("v4.budgets.%s must be an int > 0" % k)
            if v > ceil:
                raise ConfigError("v4.budgets.%s exceeds ceiling %d (FR-013)"
                                  % (k, ceil))
        for k in ("harvest", "deliver"):
            if not isinstance(v4["reflexes"][k], bool):
                raise ConfigError("v4.reflexes.%s must be a bool" % k)
        if not isinstance(v4["s1_channel"]["enabled"], bool):
            raise ConfigError("v4.s1_channel.enabled must be a bool")
        # Draft v3 knobs (FR-020/FR-021/FR-009)
        for k in ("arc_chars", "verdict_chars", "asteroid_top_n",
                  "verdict_feed_k"):
            v = v4[k]
            if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
                raise ConfigError("v4.%s must be an int > 0" % k)
        if not v4["asteroid_top_n"] <= 256:
            raise ConfigError("v4.asteroid_top_n must be ≤ 256")

    DEFAULTS = DEFAULTS


def _deep_update(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_update(dst[k], v)
        else:
            dst[k] = v


def load_obj(obj):
    return Config(obj)


def load(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return Config(data)


def config_dir():
    return os.path.join(os.path.dirname(__file__), "configs")