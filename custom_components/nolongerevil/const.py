"""Constants for the No Longer Evil integration."""

from typing import Final

DOMAIN: Final = "nolongerevil"

# Configuration keys
CONF_API_KEY: Final = "api_key"
CONF_BASE_URL: Final = "base_url"

# Default values
DEFAULT_BASE_URL: Final = "https://nolongerevil.com/api/v1"
DEFAULT_SCAN_INTERVAL: Final = 30  # seconds

# API endpoints
ENDPOINT_DEVICES: Final = "/devices"
ENDPOINT_STATUS: Final = "/thermostat/{device_id}/status"
ENDPOINT_TEMPERATURE: Final = "/thermostat/{device_id}/temperature"
ENDPOINT_TEMPERATURE_RANGE: Final = "/thermostat/{device_id}/temperature/range"
ENDPOINT_MODE: Final = "/thermostat/{device_id}/mode"
ENDPOINT_AWAY: Final = "/thermostat/{device_id}/away"
ENDPOINT_FAN: Final = "/thermostat/{device_id}/fan"
ENDPOINT_SCHEDULE: Final = "/thermostat/{device_id}/schedule"

# HVAC modes mapping
HVAC_MODE_MAP: Final = {
    "heat": "heat",
    "cool": "cool",
    "heat-cool": "heat_cool",
    "off": "off",
}

HVAC_MODE_REVERSE_MAP: Final = {v: k for k, v in HVAC_MODE_MAP.items()}

# Fan modes
FAN_MODE_AUTO: Final = "auto"
FAN_MODE_ON: Final = "on"
FAN_MODE_OFF: Final = "off"

# Temperature scales
TEMP_SCALE_CELSIUS: Final = "C"
TEMP_SCALE_FAHRENHEIT: Final = "F"

# State keys from API
STATE_CURRENT_TEMP: Final = "current_temperature"
STATE_TARGET_TEMP: Final = "target_temperature"
STATE_TARGET_TEMP_TYPE: Final = "target_temperature_type"
STATE_TARGET_TEMP_LOW: Final = "target_temperature_low"
STATE_TARGET_TEMP_HIGH: Final = "target_temperature_high"
STATE_HEATER_STATE: Final = "hvac_heater_state"
STATE_AC_STATE: Final = "hvac_ac_state"
STATE_FAN_STATE: Final = "hvac_fan_state"
STATE_FAN_MODE: Final = "fan_mode"
STATE_AUTO_AWAY: Final = "auto_away"
STATE_CAN_COOL: Final = "can_cool"
STATE_CAN_HEAT: Final = "can_heat"
STATE_TEMP_SCALE: Final = "temperature_scale"
STATE_ECO_MODE: Final = "eco_mode_enabled"
STATE_TEMP_LOCK: Final = "temperature_lock_enabled"

# Access types
ACCESS_TYPE_OWNER: Final = "owner"
ACCESS_TYPE_SHARED: Final = "shared"

# Rate limiting
RATE_LIMIT_REQUESTS: Final = 20
RATE_LIMIT_WINDOW: Final = 60  # seconds

# Platforms
PLATFORMS: Final = ["climate", "sensor", "binary_sensor", "switch"]

# Device info
MANUFACTURER: Final = "No Longer Evil"
MODEL: Final = "Smart Thermostat"

# Attributes
ATTR_SERIAL: Final = "serial"
ATTR_ACCESS_TYPE: Final = "access_type"
ATTR_REVISION: Final = "revision"
