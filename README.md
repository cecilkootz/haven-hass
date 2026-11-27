# Haven Lighting Integration for Home Assistant

This integration allows you to control Haven Lighting devices through Home Assistant.

## Features

- Automatic discovery of Haven lights
- Support for on/off control
- Brightness control
- Location-based organization

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click on "Integrations"
3. Click the "+" button
4. Search for "Haven Lighting"
5. Click "Download"
6. Restart Home Assistant

### Manual Installation

1. Copy the `haven_lighting` folder from `custom_components` to your Home Assistant's `custom_components` directory
2. Restart Home Assistant

## Configuration

1. Go to Configuration -> Integrations
2. Click "+ ADD INTEGRATION"
3. Search for "Haven Lighting"
4. Enter your Haven account credentials

## Releasing

Releases are cut by the `Release` workflow, in two ways:

- **Tag push:** bump `version` in `custom_components/haven_lighting/manifest.json`, merge to `main`, then push a matching tag:
  ```sh
  git tag v0.5.0
  git push origin v0.5.0
  ```
- **Actions UI:** go to Actions → **Release** → **Run workflow** — it tags the current `main` with the manifest version for you.

Either way the workflow validates that the tag matches the manifest version, runs the test suite, builds `haven_lighting-v<version>.zip`, and creates the GitHub release with generated notes. HACS picks up the release from the tag.

## Support

- For bugs and feature requests, please [open an issue](https://github.com/mickeyschwab/haven-hass/issues)
- For questions and discussion, visit the [Home Assistant Community](https://community.home-assistant.io/)

## License

This project is licensed under the MIT License - see the LICENSE file for details. 

## Disclaimer
The developers of this integration are not affiliated with Haven Lighting. They have created the integration as open source in their spare time on the basis of publicly accessible information. The use of the integration is at the user's own risk and responsibility. The developers are not liable for any damages arising from the use of the integration.