# No Longer Evil - Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/patricktr/NoLongerEvil-HomeAssistant.svg)](https://github.com/patricktr/NoLongerEvil-HomeAssistant/releases)
[![License](https://img.shields.io/github/license/patricktr/NoLongerEvil-HomeAssistant.svg)](https://github.com/patricktr/NoLongerEvil-HomeAssistant/blob/main/LICENSE)

A Home Assistant integration for [No Longer Evil](https://nolongerevil.com) - providing local and cloud control of your smart thermostat.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.][my-hacs-badge]][my-hacs-url]

[my-hacs-badge]: https://my.home-assistant.io/badges/hacs_repository.svg
[my-hacs-url]: https://my.home-assistant.io/redirect/hacs_repository/?owner=patricktr&repository=NoLongerEvil-HomeAssistant&category=integration

## Features

- **Climate Control**: Full thermostat control including temperature, HVAC modes, and fan modes
- **Temperature Monitoring**: Real-time temperature sensors
- **HVAC Status**: Binary sensors for heating, cooling, and fan activity
- **Away Mode**: Switch to enable/disable away mode for energy savings
- **Temperature Range**: Support for heat-cool mode with temperature ranges
- **Multiple Devices**: Support for multiple thermostats per account

## Installation

### HACS (Recommended)

1. Click the button below to open HACS:

   [![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.][my-hacs-badge]][my-hacs-url]

2. Click "Download"
3. Restart Home Assistant

### HACS Manually (only if above isn't suitable)

1. Open HACS in your Home Assistant instance
2. Click on "Integrations"
3. Click the three dots in the top right corner
4. Select "Custom repositories"
5. Add this repository URL: `https://github.com/patricktr/NoLongerEvil-HomeAssistant`
6. Select "Integration" as the category
7. Click "Add"
8. Search for "No Longer Evil" and install it
9. Restart Home Assistant

### Manual Installation

1. Download the latest release from the [releases page](https://github.com/patricktr/NoLongerEvil-HomeAssistant/releases)
2. Extract the `custom_components/nolongerevil` folder to your Home Assistant `custom_components` directory
3. Restart Home Assistant

## Configuration

### Getting an API Key

1. Go to [No Longer Evil Settings](https://nolongerevil.com/settings)
2. Navigate to the API Keys section
3. Create a new API key with the following scopes:
   - `read` - Required for reading device status
   - `write` - Required for controlling devices
4. Copy the generated API key (it starts with `nle_`)

### Adding the Integration

1. Go to **Settings** > **Devices & Services**
2. Click **+ Add Integration**
3. Search for "No Longer Evil"
4. Choose how you want to connect:
   - **No Longer Evil Cloud** — enter your `nle_` API key
   - **Self-Hosted Server** — enter your server's Control API URL (no API key needed)

## Entities

### Climate

The main climate entity provides full thermostat control:

- **Current Temperature**: The current room temperature
- **Target Temperature**: Set your desired temperature
- **HVAC Mode**: Off, Heat, Cool, or Heat-Cool (Auto)
- **Fan Mode**: Auto, On, or Off
- **Preset Mode**: Home, Away, or Eco

### Sensors

| Entity | Description |
|--------|-------------|
| Current Temperature | Current room temperature reading |
| Target Temperature | Currently set target temperature |
| HVAC Action | Current action (heating, cooling, idle, fan) |

### Binary Sensors

| Entity | Description |
|--------|-------------|
| Heating | On when the heater is actively running |
| Cooling | On when the AC is actively running |
| Fan Running | On when the fan is running |
| Home | On when home (not in away mode) |

### Switches

| Entity | Description |
|--------|-------------|
| Away Mode | Toggle away mode on/off |

## Options

You can configure the following options after installation:

| Option | Description | Default |
|--------|-------------|---------|
| Scan Interval | How often to poll for updates (seconds) | 30 |

## Self-Hosted Users

If you're running a self-hosted No Longer Evil server, choose **Self-Hosted Server**
during setup and point the integration at your server's **Control API**:

1. When adding the integration, select **Self-Hosted Server**
2. Enter your server's URL on the Control API port (default `8082`), e.g.
   `http://192.168.1.50:8082`
   - Use your server's LAN IP, not `localhost`
   - This is the same host you set as the **Public API URL** when flashing
3. No API key is required — the Control API is unauthenticated and intended for
   use on a trusted local network

> **Note:** The self-hosted Control API is a different API from the hosted cloud
> service (it addresses devices by serial and needs no API key). The integration
> selects the correct API based on the connection type you pick at setup. The
> self-hosted server is under active development, so endpoint details may change
> between server versions. If you also run an MQTT broker, the server can publish
> Home Assistant MQTT discovery records as an alternative to this integration.

### Running the NLE server as a Home Assistant add-on

If you're running the No Longer Evil server as a Home Assistant add-on, the
add-on defaults to **Ingress** and only exposes its internal proxy ports — the
Control API port (`8082`) is not reachable from this integration in that
configuration. To use this integration with the add-on:

1. Open the add-on **Configuration** tab
2. Under **Network**, expose the **Control API + Web UI** port (`8082`) on the
   host — assign it a host port (typically also `8082`)
3. Restart the add-on
4. In the integration setup, use your Home Assistant host's LAN IP with that
   port, e.g. `http://<homeassistant-ip>:8082` (not the Ingress URL)

See [#11](https://github.com/patricktr/NoLongerEvil-HomeAssistant/issues/11) for
background on this configuration.

## Troubleshooting

### Authentication Errors

- Ensure your API key is correct and has both `read` and `write` scopes
- Check that the API key hasn't been revoked in your account settings

### No Devices Found

- Make sure you have at least one thermostat registered to your account
- Verify the API key has access to your devices

### Connection Issues

- Check your network connection
- For self-hosted users, ensure your server is reachable
- The API rate limit is 20 requests per minute for API keys

### Rate Limiting

The No Longer Evil API has rate limits:
- API keys: 20 requests per minute
- User accounts: 100 requests per minute

If you see rate limit errors, increase the scan interval in the integration options.

## Support

- [No Longer Evil Documentation](https://docs.nolongerevil.com)
- [GitHub Issues](https://github.com/patricktr/NoLongerEvil-HomeAssistant/issues)

## Contributing

Contributions are welcome! Please read our contributing guidelines before submitting pull requests.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
